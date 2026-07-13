# GPU Render Optimization Paths — RayGLow Pi 5 Renderer

> **ARCHIVED 2026-07-13** (was `optimization-paths.md` at the repo root). The
> analysis is complete: items 2/3/4/8 and the skip-unchanged-uniforms half of 5
> shipped (see Status below). Everything still actionable is carried forward in
> the root **`ROADMAP.md`** — edit that, not this.

## Status — 2026-07-13: items 2, 3, 4, 5 (uniform skip), and 8 SHIPPED

The DMA-BUF investigation (item 2) validated on the Pi 5 and landed together with
the GPU resolve pass (items 3+4+8) and uniform-value caching (item 5's "skip
unchanged" half). Measured by `tools/dmabuf_probe.py` at scale 2, 48-iteration
plasma, 300 frames/mode:

| Path | Frame total | vs old default |
|---|---|---|
| glReadPixels + CPU postprocess (now `--readback legacy`) | 6.60 ms | — |
| dmabuf readback alone (sync) | 6.35 ms | 1.04× |
| dmabuf + GPU resolve pass (sync) — **the new default** | **2.72 ms** | **2.43×** |
| dmabuf + GPU resolve, ping-pong (`--readback dmabuf-pipe`) | 2.44 ms | 2.70× |

Key findings, for the record:

- **Why PBOs lost but dma-heaps win**: Mesa maps PBOs *uncached* on V3D, so reading
  the frame out of the mapping was slower than glReadPixels' own copy. A
  `/dev/dma_heap/system` buffer imported as a LINEAR ABGR8888 EGLImage gives a
  **cached** CPU mmap with explicit invalidation (`DMA_BUF_IOCTL_SYNC`) — V3D's TLB
  stores raster format natively, so the GPU renders straight into numpy-readable
  memory. No root needed (video group).
- **The fence wait is irreducible** — you can't read pixels that aren't rendered.
  The dmabuf alone therefore only saved the copy (~4%). The win came from pairing it
  with the resolve pass: with downsample+gamma+orientation on the GPU, the CPU has
  nothing left to do per frame but pack, and the *synchronous* path (zero added
  latency) captures nearly all of the pipelined path's gain.
- GPU resolve output matches the CPU postprocess within 1 LSB (float average + single
  quantization vs uint16 box-sum + 8-bit LUT); `tools/verify.py` still ALL GREEN —
  the wire contract didn't move. Dark-end gradients are slightly *better* now
  (gamma is applied to float values, quantized once).
- Item 8's flips folded into the resolve pass sampling coords; item 5's UBO half is
  NOT done (uniform value-caching alone cut the per-frame `glUniform*` ctypes calls
  by roughly half).

Remaining from this doc: item 1 (`--scale 1` once the resolve pass is judged on the
wall), item 5's UBO variant, items 6/7 (skipped deliberately — hot reload stays, audio
stays 120 Hz), item 9, and the GPU pack pass (below) as the next-wall prep.

## Summary

Lowering `--scale` from 4 to 1 or 2 alone reduced frame latency dramatically. The supersampled FBO (scale=4 → 1024×256) forces V3D to shade 4× the pixels and read back ~1MB per frame, all for a display that resolves to 256×64 physical LEDs. (**Since actioned: the default is now `--scale 2`.**)

This document captures the full bottleneck analysis of the render pipeline, ordered by expected impact.

---

## The Critical Path (per frame)

```
Shader render → glReadPixels → postprocess → hub75 pack → SPI transfer
```

Your timing stats (`render`, `pack`, `send`, `wait`) already give good visibility into each stage. The `render` column dominates because it includes GPU shader execution, the implicit sync fence of `glReadPixels`, and numpy postprocessing.

---

## Bottlenecks (ordered by impact)

### 1. Supersampling — The Biggest Lever

At `--scale 4`, you render a 1024×256 FBO and read back ~1MB per frame, then downsample to 256×64 on CPU. This means:

- **4× GPU fragment shader invocations** (1.05M vs 262K pixels)
- **4× glReadPixels bandwidth** (~1MB vs ~256KB)
- **Heavier numpy postprocess** over a 4× larger buffer

For an LED wall, pixel-exact rendering (`scale=1`) is visually indistinguishable — there's no sub-pixel anti-aliasing benefit since each "pixel" is a physical LED cell. Even `scale=2` cuts readback to ~500KB and GPU work by 75%.

**Action**: Use `--scale 1` or `--scale 2`. Already validated — this was the single biggest latency reduction.

### 2. `glReadPixels` — The Dominant Sync Point

`render/output.py:81-83` — reading the FBO forces an implicit GPU-CPU pipeline fence. Even with V3D's unified memory, the CPU thread blocks until rendering is complete and data is coherent. This is the single biggest remaining cost after lowering scale.

PBOs were already tried (`output.py:86-110`) and measured slower — your diagnosis of uncached mapped buffers was correct. But other approaches exist:

- **DMA-BUF / EGL image zero-copy**: Mesa on Pi supports `EGL_EXT_image_dma_buf_import`. Export the FBO texture as a DMA-BUF and memory-map it with numpy, eliminating the readback sync entirely. This is how Wayland compositors avoid stalls on V3D.
- **Render at panel resolution** (i.e., scale=1): eliminates downsample, makes `glReadPixels` return exactly the bytes you need.

### 3. Postprocess as a Shader Pass

The box-sum downsample + gamma LUT + flip (`output.py:65-75`) costs ~3-5ms of numpy work per frame. Replace it with a **downsampling shader pass**: render the supersampled FBO into a second FBO at panel resolution using a fragment shader that averages each texel block and applies gamma correction. Benefits:

- Pushes all postprocessing to V3D
- Eliminates CPU-stage entirely
- `glReadPixels` returns a contiguous (H,W,3) buffer directly
- Combines with point 4 (gamma in shader) for free

### 4. Move Gamma Correction Into the Shader

Currently gamma is applied by the packer's LUT lookup (`hub75.py:113`). The readback runs at LINEAR because the RP2350 firmware applies its own CIE LUT downstream. But if you apply gamma in the final image shader, the numpy fancy-indexing `self._lut[boxed[::-1, :, :3]]` (`output.py:74`) becomes unnecessary — it creates an intermediate array via advanced indexing.

Moving gamma to the shader also lets you combine it with the downsample pass (point 3): one shader that reads supersampled pixels, averages them, applies gamma, and outputs panel-resolution uint8. The packer then does a simpler direct bit-extraction without LUT lookup.

### 5. Uniform Update Overhead

`passes.py:206-227` — each pass issues ~15 individual `glUniform*` ctypes calls per frame. At scale=4 with multipass, that's potentially 5× as many calls. Each ctypes call has Python-side argument packing overhead on the V3D driver path.

**UBO (Uniform Buffer Object)**: Pack all uniforms into a single `GLfloat[]` array and upload once with `glBufferSubData`. GLES3 supports UBOs (`GL_UNIFORM_BUFFER`). The Shadertoy preamble would need to use an `uniform block`, but it eliminates 15 ctypes calls per pass and replaces them with one buffer update.

**Skip unchanged uniforms**: Several don't change between frames:
- `iDate` — changes once per second at most
- `iResolution` — constant for the lifetime of a shader
- Sampler indices (`iChannelN`) — constant per shader
- Only `iTime`, `iTimeDelta`, `iFrame`, and channel textures actually update each frame

Caching which uniforms changed would cut uniform uploads by ~60% on steady-state frames.

### 6. Audio Channel FFT Per Frame

`textures.py:122-135` — every frame, `AudioChannel.update()` runs an rFFT(512) + windowing + log10 + two `np.interp` calls + texture upload. At 120fps that's 120 FFTs/sec.

**Update at lower rate**: Audio doesn't change fast enough visually to need 120Hz updates. Update the audio texture at 30Hz (every 4th frame) and hold the last values. This saves 75% of FFT + interp work with no visible difference on an LED wall.

**Pre-allocate interp outputs**: `np.interp` allocates intermediate arrays each call. Pre-allocating the output buffers would avoid allocation pressure, though at 120fps this is minor compared to the FFT itself.

### 7. Hot Reload File Stat Every Frame

`render/reload.py:9-28` / `__main__.py:366` — `maybe_reload()` calls `watcher.changed()` which does `os.stat()` on every shader file each frame. At 120fps that's 120 stat syscalls/sec per tracked file (usually 2-5 files for multipass).

**Throttle to once per second**: In production, hot reload is a development convenience. A simple counter or timestamp check can reduce this to ~1 syscall/sec/file with no functional difference.

### 8. Array Copies in the Main Loop

`__main__.py:374-378` — flip operations `buf[::-1]` and `buf[:, ::-1]` create views (cheap), but `np.ascontiguousarray(buf)` forces a full copy of the frame (~49KB for 256×64×3). If flips are enabled, that's an extra allocation + memcpy per frame.

**Make readback output correct orientation**: Flip in the shader (invert Y in the epilogue) or in the downsample pass so `glReadPixels` returns data already oriented correctly. Eliminates both the flip and contiguification step.

### 9. Single-Chain Fold Loop

`hub75.py:207-211` — if using single-chain mode, the serpentine fold iterates in a Python for-loop over panels. For a 6-panel rig this is negligible (~0.1ms), but worth noting if you scale up. A vectorized numpy approach (pre-computed index array with advanced indexing) would eliminate the loop.

---

## Quick Wins (Low Effort, Good Impact)

| Change | Effort | Expected Benefit |
|--------|--------|-----------------|
| `--scale 1` or `2` | Trivial | Already validated — large latency reduction |
| Throttle audio texture to 30Hz | Low (~5 lines) | Saves FFT + interp on 75% of frames |
| Disable hot reload in production | Low (flag or counter) | Eliminates stat syscalls at 120fps |

## Medium Effort, Good Impact

| Change | Effort | Expected Benefit |
|--------|--------|-----------------|
| Downsample shader pass | Medium (new GLSL + FBO) | Eliminates CPU postprocess stage entirely |
| Gamma in final shader | Low-Medium (preamble change) | Simplifies packer, combines with downsample |
| Skip unchanged uniforms | Medium (tracking logic) | Cuts uniform uploads ~60% per frame |

## High Effort, Potentially Transformative

| Change | Effort | Expected Benefit |
|--------|--------|-----------------|
| DMA-BUF zero-copy readback | High (EGL extension + mmap) | Removes the `glReadPixels` sync fence entirely |
| UBO for all uniforms | Medium-High (preamble + ctypes) | Single buffer upload replaces 15+ ctypes calls per pass |

---

## Scaling to the next wall (512×128 or 384×96, p4) — 2026-07-13 audit

Where each stage lands at 4× / 2.25× today's pixel count:

| Stage | 256×64 today | 384×96 | 512×128 | Verdict |
|---|---|---|---|---|
| GPU shade (scale 2) | 512×128 px | 768×192 | 1024×256 | 512×128@scale2 = today's scale-4 area — heavy shaders will hurt; scale 1 may become the default there |
| Readback (RGBA, scale 2) | 256 KB | 576 KB | 1 MB | the sync fence grows linearly — this is what makes the GPU pack pass (below) matter |
| numpy pack | 64 KB out | 144 KB | 256 KB | ~linear in pixels; ~4× today's `pack` ms at 512×128 |
| Link (4-lane @ clkdiv 3, 33 MB/s) | 2.0 ms | 4.4 ms | 7.7 ms | fine (overlapped); clkdiv 2 = 50 MB/s halves it if the HAT's SI allows |
| RP2350 refresh (25 MHz px clk, 2 chains) | ~680 Hz | ~340 Hz | ~190 Hz | fine; 37.5 MHz buys ~1.5× |
| **RP2350 SRAM (520 KB total)** | 128 KB (2× 64 KB fb) | **288 KB — fits** | **512 KB — does NOT fit** | **the hard ceiling.** fb bytes = wall_px/2 × B, double-buffered |

So: **384×96 runs on the current RP2350 rig unchanged** (bigger consts, generalized
serpentine fold for 3 panel-rows on 2 chains). **512×128 does not fit** at 8-bit BCM
double-buffered — the outs are B=6 (384 KB, visible banding risk in the low end),
single-buffering (tear/race risk), or the FPGA translator (ULX3S/ECP5 with real RAM) —
which was the plan anyway. PSRAM on the RP2350B is not a real out: the scan-out DMA
needs jitter-free ~50 MB/s continuous reads that QSPI PSRAM can't guarantee.

### The one big Pi-side lever left: pack on the GPU

Combine items 3+4 above and go further — a final "pack pass" fragment shader (GLES 3.1
integer ops) that box-averages the supersampled texture, applies the CIE LUT via a
256-entry `usampler2D` texelFetch (bit-exact vs `lut.rs`), extracts the BCM planes, and
writes the u16 cells straight into an RGBA8 target whose bytes ARE the wire stream
(fold the PIO nibble-swap in for free). `glReadPixels` then returns the 64 KB payload
directly:

- readback shrinks 4× (scale 2) — 1 MB → 256 KB on the 512×128 wall
- the entire numpy postprocess + pack stage disappears (`pack` → ~0)
- verification story survives: extend `tools/verify.py` to diff the GPU pack against
  `hub75.pack` on random frames

This is the prep work that makes 512×128 viable on the Pi 5 side.

- **Box-sum integer downsample** (`output.py:70-71`): Replacing float `.mean()` (~13ms at scale=4) with uint16 box-sum + LUT was a great call — ~10× faster.
- **SendPipe overlap** (`__main__.py:228-285`): The depth-1 pipeline that overlaps SPI transfer with GPU rendering is well-designed. If `wait` hugs 0 in your stats, the link is fully hidden behind render.
- **PBO correctly disabled**: Your analysis of why PBOs are slower on V3D (unified memory = no bus stall to hide, uncached mapped buffer) was spot-on.
- **Thread pinning** (`__main__.py:60-67`): Pinning the render thread and letting the send worker float is the right approach for Pi 5's big.LITTLE cores.
