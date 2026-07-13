# Tuning Guide — rayglow over the RP2350 link

This is the knob reference for the split rig: **rpi5 renders + packs**, ships packed
bit-plane frames to the **rp2350b** (4-lane parallel PIO bus by default, SPI as the
fallback), which drives the 256×64 HUB75 wall with a zero-CPU PIO+DMA engine.

## Mental model: two sides, one link

| | **rpi5** (`rayglow` package) | **rp2350b** (`firmware/`) |
|---|---|---|
| Owns | **Content** — pixels, gamma, orientation, resolution, fps | **Timing** — HUB75 pixel clock, BCM/OE brightness, refresh |
| Change via | `config.py` + CLI flags | editing a `.rs` const |
| Takes effect | next run (no reflash) | **only after `cargo run` reflash** |

The link carries *only frame content* (a 64 KB packed bit-plane buffer — identical
bytes over PIO or SPI). So anything about *how the panels are lit* (brightness, clock,
refresh) is firmware and needs a reflash; anything about *what is drawn* is rpi5-side
and is live.

---

## Reading the stats line (what is clamping my FPS?)

The renderer prints a 5-second rolling line:

```
  fps  render  pack  send  wait   (PIO floor 2.0ms @ clkdiv 3)
```

- **`render`** — GLSL execution + resolve pass + readback (main thread). The readback
  is a zero-copy dma-heap mmap by default (`--readback auto`); gamma + orientation
  ride along in the resolve pass, so there is no CPU postprocess.
- **`pack`** — numpy bit-plane packing (main thread).
- **`send`** — the worker thread's actual wire time (overlapped with the next render).
- **`wait`** — how long the main thread blocked on the *previous* transfer. This is
  the only part of the link that leaks into the critical path.
- **floor** — the theoretical minimum wire time for the frame at the current clock.

Diagnosis:

| Symptom | Limiter | Lever |
|---|---|---|
| `wait` ≈ 0, `render` dominates | **GPU / shader** | drop `--scale`, simplify the shader, fewer buffer passes |
| `wait` ≈ `send` | **the link** | lower `--pio-clkdiv` (faster clock), or `--transport spi` → raise `--spi-hz` |
| `pack` creeping up | **CPU packing** | scales with pixel count; vectorized already — big walls want GPU-side packing |
| fps pinned at the cap, all columns low | **`--fps` cap** | raise `--fps` (the READY handshake still self-paces) |
| Pi-side fine but wall stutters | **firmware** | check the RTT log's `rx fps (drops N)` — drops = corrupt/short frames |

The firmware telemetry (`cargo run --bin phase6-parallel` with the probe attached)
prints `rx fps` once a second — a live confirmation of what the rp2350b actually
receives and flips in.

---

## rpi5 / rayglow knobs — no reflash

### CLI flags (`python -m rayglow.render <shader> [flags]`)

| Flag | Default | What it does / when to reach for it |
|---|---|---|
| `--transport` | `pio` | `pio` = 4-lane RP1-PIO parallel bus (needs `phase6-parallel` firmware + `piobridge/libpioshim.so`). `spi` = 1-lane fallback (needs `phase5-spi`). |
| `--pio-clkdiv` | 3 | RP1-PIO clock divisor; per-lane rate ≈ 200 MHz/(2·div), so the 64 KB frame floor ≈ 2 ms at div 3, 1.3 ms at div 2. **Lower = faster, more SI risk.** |
| `--spi-hz` | 24 MHz | SPI clock (fallback path only). Higher = more throughput, more wiring SI risk. |
| `--scale` | 2 | GPU supersample factor (1–16). 2 is the sweet spot for LED walls; 4 costs ~4× the GPU + readback for no visible gain on physical LEDs. Drop to 1 if a heavy shader still chugs. |
| `--fps` | 120 | Frame-rate cap. The link self-paces off the rp2350b's READY line; this just stops rendering frames nobody asked for. |
| `--gamma` | 1.0 | Dry-run preview gamma only. Wall runs ignore it — they bake `PACK_GAMMA` into the GPU resolve pass (or, with `--readback legacy`, into the packer's LUT). |
| `--readback` | `auto` | GPU→CPU frame path. `auto` = zero-copy dma-heap readback (falls back to `glReadPixels` off-Pi). `dmabuf-pipe` = ping-pong two buffers, reads frame N−1 while N renders — fastest, but +1 frame latency. `legacy` = the original full-size `glReadPixels` + numpy postprocess, for A/B or regression hunting. |
| `--width` / `--height` | 256 / 64 | Render size (defaults to the full wall from `config.py`). Only touch for experiments. |
| `--duration` | 0 (forever) | Stop after N seconds. |
| `--loop SECONDS` | — | Cycle every standalone `.glsl` in the shader's folder. |
| `--channel0..3`, `--no-listen` | — | Bind iChannels (audio / milk / spectrum / noise / image); `--no-listen` = synth audio only. |

### `config.py` (`rayglow/feed/config.py`) — the structural settings

| Constant | Now | Meaning |
|---|---|---|
| `ROWS`,`COLS`,`CHAIN`,`PARALLEL_CHAINS` | 32,64,4,2 | Panel geometry. `WALL_WIDTH`/`WALL_HEIGHT` derive from these (256×64). Change only if the panel count changes. |
| `PACK_GAMMA` | 2.1 | The CIE gamma the wall gets — baked into the **GPU resolve pass** by default (packer LUT in `--readback legacy`). Lower = brighter mids / less contrast, higher = deeper blacks. **Must stay equal to the firmware `lut.rs` gamma** so the look matches firmware-rendered demos and the byte-match golden (`tools/verify.py`). |
| `FLIP_H` / `FLIP_V` | False / False | Orientation. Flip these if you re-mount or re-cable; confirm with `rayglow.spi_test`. |
| `BITDEPTH` | 8 | BCM planes. **Must equal firmware `B`.** |
| `SINGLE_CHAIN` + `CHAIN_ORDER` / `ROW_ROTATE_180` | False | Single-chain serpentine fallback rig (Adafruit-HAT era). Leave False on the two-chain HAT. |

> Gamma note: on the streaming path the firmware does NOT gamma-correct — it DMAs the
> pre-packed bit-planes straight to the panels. Gamma is applied entirely on the rpi5:
> by the GPU resolve pass (default — quantizes to 8 bits once, from float, so dark
> gradients come out smoother than the old 8-bit→LUT path), or by the packer's
> bit-exact `lut.rs` replica in `--readback legacy`. The firmware's own LUT only
> matters for firmware-rendered demos like `phase4-anim`.

---

## rp2350b / firmware knobs — require a reflash

Edit `firmware/src/bin/phase6_parallel.rs`, then from the `firmware/` dir:

```fish
cargo run --release --bin phase6-parallel --features two-chain   # full wall
```

| Const | Now | Meaning / tradeoff |
|---|---|---|
| `OE_GAIN` | 64 | **The brightness knob.** Scales the BCM output-enable (lit) intervals; brightness climbs ~linearly until the top plane's on-time approaches the shift time, then it starts eating refresh. |
| `DATA_CLK_DIV` | `(3,0)` = 25 MHz | **HUB75 pixel clock** = sysclk / (2·div). Faster = brighter + higher refresh, more SI risk down the chain. `(2,0)` = 37.5 MHz validated clean through the HAT's '245 buffers. |
| `PANELS_IN_CHAIN` | 4 | Panels per chain; `W = 64 ×` this. The A/B knob (below). |
| `RX_STALL_US` | 50 ms | Ingest watchdog — a started-then-stalled frame is aborted and dropped instead of wedging the link. |
| `USE_CS` | false | CS framing reserve (needs the GP25 jumper + `--pio-cs`). READY + fixed frame size delimit frames without it. |

## A/B testing: 4-panel row vs full 8-panel wall

Both sides derive the frame byte-count from the geometry, and the RX DMA waits for
**exactly** that many bytes — a mismatch desyncs the link. So a config change is
always a *pair* of changes:

| Rig | Firmware (reflash) | Pi (`config.py`, live) | Frame |
|---|---|---|---|
| Full wall, two chains (production) | `--features two-chain`, `PANELS_IN_CHAIN=4` | `SINGLE_CHAIN=False`, `PARALLEL_CHAINS=2` | 64 KB |
| One 4-panel row, single chain | default features, `PANELS_IN_CHAIN=4` | `SINGLE_CHAIN=True`, 4-entry `CHAIN_ORDER`, `PARALLEL_CHAINS=1` | 32 KB |
| 8-panel serpentine, single chain (Adafruit-HAT era) | default features, `PANELS_IN_CHAIN=8` | `SINGLE_CHAIN=True`, 8-entry `CHAIN_ORDER` | 64 KB |

The Pi side needs no reflash — the packer sizes the payload from the frame it's
handed (`pack()` infers width; `to_single_chain` does the serpentine fold), and the
PIO shim sizes its DMA buffer from the first payload. The firmware side is
compile-time (`W`/`H` are const generics), hence the reflash.

---

## Quick recipes

- **Too dim** → raise `OE_GAIN` (firmware, reflash), or lower `DATA_CLK_DIV` for a
  faster, brighter clock (SI permitting).
- **Flicker / low refresh** → lower `DATA_CLK_DIV` div (faster pixel clock).
- **SI artifacts** (down-chain vertical bars, scan-half noise) → raise
  `DATA_CLK_DIV` div (slower clock); check grounding (`hardware/POWER-AND-GROUNDING.md`).
- **Heavy shader chugs** → drop `--scale` (2→1); check `render` in the stats line.
- **Want more fps** → read the stats line first (see above), then attack the actual
  limiter: `--scale` for render, `--pio-clkdiv` for the link. If `render` still
  dominates, `--readback dmabuf-pipe` hides the GPU behind the pack stage for one
  frame of added latency.
- **Colors look off** → confirm `PACK_GAMMA` matches the firmware LUT
  (`tools/verify.py` proves it); A/B against `--readback legacy` to rule the resolve
  pass in or out (expect ≤1 LSB of difference).
- **Image flipped / mirrored** → `FLIP_H` / `FLIP_V` in `config.py`.
- **Link drops / desync** (`drops` climbing in the RTT log) → raise `--pio-clkdiv`
  (slower), check the J4 lane bundle's ground return, or fall back to `--transport spi`
  to isolate.

---

## Diagnostics

- Renderer stats line — the per-stage breakdown (see "Reading the stats line").
- `python -m rayglow.spi_test [--flipv --fliph --spi-hz N]` — static test pattern
  straight through `pack → SPI → wall`, no GL (SPI fallback path; flash `phase5-spi`).
  Corner colors (white TL, red TR, green BL, blue BR) verify orientation.
- `tools/pio_ramp.py` — a known byte ramp over the parallel bus, for nibble/lane-order
  checks against the firmware's `RX_DEBUG_BYTES` dump.
- `python -m rayglow.render <shader> --dry-run` — render to a GIF headlessly, no
  hardware, to preview geometry/shader.
- Firmware RTT log (`cargo run --bin phase6-parallel ...`) prints `rx fps (drops N)` —
  live confirmation the link is locked.
