> **⚠️ HISTORICAL DESIGN RECORD — superseded.** This is the original project
> handoff brief, from when RayGLow was a from-scratch MilkDrop port. It is kept for
> provenance: the MilkDrop reverse-engineering (§2) and the rationale for building from
> scratch (§3) are still the best reference for those. But the architecture has since
> moved on — the renderer is now GLSL (`rayglow.render`), not the feedback-buffer core
> described in §4; that core is retired in `rayglow/legacy/`. The §5 packet spec
> documents the **v0** ancestor (the live protocol is **v1**, 564 bytes — see
> `sender/README.md`). Where this disagrees with the top-level README / package docs,
> the README wins.

# Project Milk-Pi

MilkDrop-style audio-reactive visualizations, rebuilt from scratch, rendered on an RGB LED
matrix. Audio plays on the desktop; the Pi only renders.

This document started as a handoff brief from `will-desktop` (written after getting
MilkDrop3 running under Wine and studying its source — `code/` in this repo:
https://github.com/milkdrop2077/MilkDrop3) and is now the shared project record. Both
machines keep a copy: `~/Projects/MilkDrop3/project-milk-pi.md` (desktop) and
`~/rpi-rgb-led-matrix/MilkDrop3/project-milk-pi.md` (Pi). **STATUS: working end-to-end on
the panel with real music (June 2026).**

---

## 1. System architecture

```
will-desktop (192.168.1.105, CachyOS, PipeWire)          raspberry pi 4b (192.168.2.108)
┌─────────────────────────────────────────┐        ┌──────────────────────────────┐
│ music playback ──▶ sink monitor source  │        │  UDP listener (latest-wins)  │
│        │                                │  UDP   │        │                     │
│  feature daemon: capture → FFT →        │ ─────▶ │  renderer: feedback-buffer   │
│  bass/mid/treb (+_att) + waveform       │ :5005  │  core loop (NumPy/OpenCV)    │
│  → 556-byte packet, unicast, ~60 Hz     │        │        │                     │
└─────────────────────────────────────────┘        │  hzeller rpi-rgb-led-matrix  │
                                                   │  4x 64x32 P6 HUB75 = 256×32  │
                                                   └──────────────────────────────┘
```

Division of labor:
- **Desktop daemon** (`~/Projects/milk-pi/sender.py`, uv project): captures the PipeWire
  monitor source, extracts audio features, blasts UDP. Negligible CPU cost there (~1.7%).
- **Pi renderer** (`~/rpi-rgb-led-matrix/will-rpi-custom/milk/`): receives features, runs
  the visualization core loop, drives the matrix. The Pi is already heavily loaded
  bit-banging HUB75 via the hzeller library (with hardware mods), so it does **zero audio
  work** — no capture, no FFT.

Why features instead of streaming PCM: drift and jitter only matter when reconstructing a
continuous signal. Features are **stateless per frame** — no ring buffer, no clock-drift
resampling, no jitter buffer. Lost/late packet → render with the previous values; at 60 Hz
one held frame is invisible. UDP used the way UDP wants to be used.

Network notes: **unicast** to the Pi's IP (multicast across VLANs needs IGMP/mcast-routing
cooperation from the Cisco 3850 + OPNsense — not worth it for one receiver). Desktop→Pi
UDP 5005 crosses the User→IoT VLAN boundary with no extra firewall rule (verified).

---

## 2. How MilkDrop actually works (codebase findings)

Source layout (`code/`):
- `audio/` — WASAPI loopback capture (Windows-only, irrelevant to us, but the *interface*
  it feeds is the spec for our packet)
- `ns-eel2/` — Nullsoft Expression Evaluator: preset equations are JIT-compiled to native
  x86 at preset load (`nseel-compiler.c`). Preset "code" is control logic, not drawing.
- `vis_milk2/milkdropfs.cpp` — essentially the entire renderer. The one file to study.

### The mental model

MilkDrop is a **feedback loop on a framebuffer** (an IIR filter on the image):

```
prev_frame ─▶ warp (resample through displacement field) ─▶ decay ─▶ draw audio geometry ─▶ next_frame
                                                                            │
                                                              composite pass ─▶ display
```

Frame N's output is frame N+1's input texture. Trails, tunnels, fractal zooms, smears are
all emergent from this loop. Preset code never touches pixels — it steers ~10 scalars.

### Audio interface (the spec for our packet)

`vis_milk2/plugin.h:61`: 576-sample PCM windows, 2 channels. FFT'd, then collapsed to
**three band energies**. Set into preset variables at `vis_milk2/milkdropfs.cpp:483`:

- `bass`, `mid`, `treb` — instantaneous band energy **normalized by its own running
  average** (`imm_rel`): 1.0 = "typical for this song right now". This auto-gain is why
  presets work across genres/volumes. Ported exactly (plugin.cpp:8750) in both
  `fake_sender.py` and `sender.py`.
- `bass_att`, `mid_att`, `treb_att` — temporally smoothed versions (`avg_rel`).
- Plus `time`, `frame`, `fps`. That is the **entire** audio→preset interface: six scalars.

The raw waveform is used in exactly one other place: drawn directly as a polyline
(`DrawWave(mysound.fWave[0], mysound.fWave[1])`, `milkdropfs.cpp:1056`).

Exact analysis chain (ported in `sender.py`, validated with sine tones): 576 left-channel
samples → Hann envelope (`fft.cpp` InitEnvelopeTable, power=1) → zero-padded 1024-pt FFT →
512 magnitude bins × log equalize table (`-0.02*ln((512-i)/512)`) → three equal LINEAR
thirds of the bottom half (`plugin.cpp:8736`): bins [0:85], [85:170], [170:256] ≙
0–4 / 4–8 / 8–12 kHz at 48 kHz. (fft.cpp's comments recommend octave bands; the actual
code never uses them. We replicate the code, not the comment. Beware `myfft.Init(576,
512, -1)` — the `-1` lands on `bEqualize`, which is truthy, so equalize is ON.)

### Render pass order (`milkdropfs.cpp:751`, `RenderFrame`)

1. Run per-frame equations (`:873`) — audio vars in; `zoom, rot, dx, dy, cx, cy, sx, sy,
   warp, decay, wave_*` out. `q1..q32` carry values from per-frame code to per-vertex code.
2. Motion vectors overlay (`:965`) — optional.
3. **WarpedBlit** (`:1013`) — the heart. Previous frame rendered through a coarse mesh
   (up to 192×144, `md_defines.h:49`; typically ~48×36). The "per_pixel" equations are a
   historical lie — they run **per mesh vertex** (`:1750`), computing where each vertex
   *samples from* in the old frame: zoom = radial UV scale about (cx,cy), rot rotates UVs,
   dx/dy translate, warp adds drunken-sine displacement (`:1797-1812`; four incommensurate
   cosines with magic constants at `:1702` — `11.68, 8.77, 10.54, 11.49` — so the wobble
   never visibly repeats). GPU bilinear interpolation fills between vertices. The mesh
   exists only because 2001 GPUs couldn't afford per-pixel math.
4. Geometry injection (`:1054-1057`) — custom shapes, custom waves, the basic PCM
   waveform polyline, sprites. This is where audio energy enters the feedback loop.
5. **Composite pass** (`ShowToUser`, `:1081`) — gamma, video echo, hue shift. Outside the
   feedback loop: beautifies, doesn't accumulate.
6. Texture swap; next frame.

### Preset files

`.milk` files are INI format. Relevant blocks: scalar defaults, `per_frame_N=` lines,
`per_pixel_N=` lines (per-vertex), wave/shape sections. Presets with `warp_1=`/`comp_1=`
HLSL shader blocks are arbitrary GPU programs — the shader *cannot* be replicated by this
architecture, but (June 2026 finding, from running the dotmilk transpiler against a 311-
preset library): the *equations* of shader-era presets still drive the motion faithfully;
what's lost is shader-side *coloring*. Presets whose only light source is the shader
render dark and get triaged out — ~60% of the library survives at the user-approved bar.

---

## 3. Why from scratch (vs. frame capture or projectM)

Considered and rejected for the primary path:
- **Window/frame capture + PIL downscale**: works, but the Pi/desktop split gets ugly
  (capture on desktop → now you're streaming video, 100x the bandwidth and latency
  machinery), and it teaches nothing.
- **projectM** (open-source MilkDrop reimpl, GLES, runs on a Pi natively; people have piped
  it to hzeller matrices): pragmatic fallback if from-scratch stalls, and the only route to
  full shader-era preset fidelity. But the Pi is already CPU/timing-constrained from HUB75
  bit-banging, and GL-render-then-readback adds contention.

From scratch won because the core is **unusually tractable**: at matrix resolution
(256×32 = 8k px) true per-pixel displacement is affordable in vectorized NumPy /
`cv2.remap` — the mesh trick is skipped entirely (per_pixel runs truly per pixel). The
interesting motion is low-frequency by construction (it had to survive mesh interpolation),
so almost nothing is lost at LED resolution. What *does* die at P6 pitch: fine waveform
detail — fatter lines, fewer points than the original 576.

---

## 4. Pi-side renderer framework (as built — kept as design reference)

Core loop (Python + NumPy + OpenCV):

1. `buf`: float32 array, H×W×3. Geometry from `milk/config.py` (single source of truth;
   `CHAIN=4` is the one knob if panel 5 returns).
2. Each frame:
   a. Read newest feature packet (nonblocking, latest-wins, see §5). No packet for 0.5 s →
      synth fallback (gentle ~1.0-hovering LFOs) so the display never freezes.
   b. Update steering parameters (zoom/rot/dx/dy/warp/decay/wave color...) from features.
   c. `cv2.remap(buf, map_x, map_y, INTER_LINEAR)` — the warp, with params-hash caching
      of coordinate grids (`milk/warp.py` is a faithful per-pixel port of
      `milkdropfs.cpp:1698-1837` including the exact warp constants).
   d. `buf *= decay`; optional darken-center, borders.
   e. Draw waveform/shapes from packet's wave data.
   f. **`buf.clamp()` after the draw stage** — load-bearing: MilkDrop's 8-bit feedback
      texture implicitly clamped at 1.0 every frame; without it, waveform pixels
      equilibrate at brightness/(1−decay). The source never states this property.
   g. Tone-map to uint8 (`--gamma`, default 1.2), push via hzeller, keep float buf.
3. Performance: hzeller's GPIO thread owns core 3; render pinned to core 0
   (`config.RENDER_CORE`). Headless: ~440 fps tunnel / ~290 fps wobble at 256×32 — huge
   headroom over 60.

Structure mirrors MilkDrop's host/preset split: engine (buffer, warp, draw, matrix out) vs
presets. Two kinds of presets:
- Hand-written: `milk/presets/tunnel.py`, `wobble.py` — fn(FeatureState) → steering dict;
  tunables grouped at top (DECAY, ZOOM_BASS, HUE_SPEED…).
- **Real .milk: `milk/dotmilk/`** — .milk parser + full NS-EEL→NumPy transpiler
  (loop/while/megabuf/gmem/`x[i]` memory syntax; 311/311 of
  `milk/presets/dotmilk-presets/` transpile) + MilkPreset runtime (per_frame scalar,
  per_pixel truly per-pixel vectorized — no mesh), main-wave modes 0–7, custom waves
  (vectorized w/ auto-fallback to sequential for cross-point accumulators), custom shapes
  (vectorized instances, textured = affine feedback resample), borders, darken-center.
  8:1 aspect caveat: custom-wave canvas squashed-to-fit via `config.WAVE_FIT`
  (1.0 fit / 0.0 faithful-crop).

Run (on the Pi):
- Playlist: `sudo ~/rgbvenv/bin/python -m milk --milk <dir|keepers.txt> [--duration N]
  [--shuffle]` — keys n/p/space/r/q; presets preloaded pre-privilege-drop.
- Hand-written: `sudo ~/rgbvenv/bin/python -m milk --preset tunnel`
- Triage a preset library: `python -m milk.dotmilk.triage <dir> -o <out>` → grades every
  preset (sheets + report + keepers.txt).

---

## 5. Feature packet — DRAFT v0 (in production; bump to v1 only if layout changes)

Both ends of this contract exist and interoperate: `sender.py` (desktop, real audio) and
`milk/receiver.py` (Pi; 556-byte size asserted at import, seq-wrap handled).
`milk/fake_sender.py` remains the music-free test harness — same struct, synthesized
features, MilkDrop-exact AutoGain.

Unicast UDP, one packet per tick at ~60 Hz, little-endian, fixed layout:

```
offset  type        field
0       uint32      magic = 0x4D494C4B ("MILK")
4       uint16      version = 0
6       uint16      flags (reserved)
8       uint32      seq            (wraps; receiver drops stale/reordered)
12      float32     t              (sender monotonic seconds)
16      float32     bass           (imm_rel-style: normalized by running avg)
20      float32     mid
24      float32     treb
28      float32     bass_att       (smoothed)
32      float32     mid_att
36      float32     treb_att
40      float32     vol            (overall, normalized)
44      float32[128] wave          (mono waveform window, downsampled from 576, ±1.0)
556     = total bytes
```

~33 KB/s at 60 Hz. Receiver rules: bind, `recvfrom` nonblocking each frame, drain the
socket keeping only the highest `seq`, render with it; if nothing new, render with previous
values (do NOT block the render loop on the network).

Auto-gain semantics (defined by `AutoGain` in both senders, ported from plugin.cpp:8750):
per band, `imm_rel = imm/long_avg` (long_avg retention 0.992 @30fps-equivalent after a
50-frame fast-converge at 0.9), `avg_rel = avg/long_avg` (avg attack 0.2 rising / 0.5
falling). Values hover ~1.0, spike on hits.

---

## 6. Latency budget (audio-out → photon)

| Stage | Cost |
|---|---|
| PipeWire capture quantum (desktop) | 5–10 ms (tunable, `PIPEWIRE_LATENCY=256/48000`) |
| FFT window | ~12 ms — inherent measurement window (MilkDrop uses 576 @ 44.1k too) |
| UDP, LAN | <1 ms wired; WiFi OK in practice (see §7) |
| Pi render + hzeller refresh | 2–10 ms |
| **Total** | **~15–25 ms** — beat-sync stays tight under ~40–50 ms |

The network is the cheapest line item. The war is won in capture quantum and render rate.

---

## 7. Resolved facts & shelved plans

- Geometry: **4 panels chained = 256×32** (`CHAIN=4` in `milk/config.py` is the one knob
  if panel 5 returns). Pi headless perf: ~440 fps tunnel / ~290 wobble.
- Pi: **192.168.2.108** (IoT VLAN, WiFi, DHCP — consider a dnsmasq reservation since
  sender.py defaults to this IP), UDP port **5005**. Desktop→Pi UDP 5005 crosses VLANs
  with no OPNsense rule needed (verified via ICMP-unreachable probe).
- Pi code: `/home/will/rpi-rgb-led-matrix/will-rpi-custom/milk/` (mounted on desktop at
  `~/local-mount/rpi4/`).
- Preset library: `milk/presets/dotmilk-presets/` (311 presets; the MilkDrop3 repo clone
  ships none — the desktop Wine install has the full set at
  `~/.wine-milkdrop3/drive_c/users/will/Desktop/MilkDrop 3.31/Milkdrop3/presets/`).

**Latency hardening — SHELVED (June 2026).** End-to-end feels tight in practice; the
4–250 ms idle ping variance is WiFi power-save doze (packets buffer at the AP between DTIM
beacons), and the 60 Hz stream keeps the radio awake during actual use. Evaluated and
rejected: **BLE** (267 kbps stream vs BLE's practical BlueZ throughput; 7.5–50 ms
connection-interval latency floor; 556 B > ATT MTU → fragmentation; Pi 4's CYW43455 shares
one antenna between WiFi and BT — coexistence makes both worse). If/when revisited, in
order:
1. **Free:** disable WiFi power-save on the Pi — `sudo iw dev wlan0 set power_save off`;
   persist via `nmcli con mod <wifi-con> 802-11-wireless.powersave 2` (Bookworm/NM).
2. **Clean:** point-to-point Ethernet. will-desktop has an idle dual-port Intel X540
   10GBASE-T (`enp132s0f0/f1`); 10GBASE-T autonegotiates down to the Pi's 1GbE. One 5-ft
   cable, private /30, **no switch port, no VLAN, no OPNsense changes** — the subnet
   exists only on the wire: desktop `nmcli con add type ethernet ifname enp132s0f0
   con-name milk-ptp ipv4.method manual ipv4.addresses 10.10.0.1/30`; Pi eth0 static
   10.10.0.2/30 (no gateway → no routing ambiguity, WiFi stays for management); then
   `sender.py --host 10.10.0.2`.

## 8. Status

- [x] MilkDrop3 running on will-desktop (Wine, dedicated prefix `~/.wine-milkdrop3`,
      `milkdrop3` launcher) — reference visuals live
- [x] Pipeline reverse-engineered (this doc)
- [x] Pi: renderer framework + fake-sender harness — built & verified headless
- [x] Desktop: feature daemon — `~/Projects/milk-pi/sender.py` (uv project). Faithful FFT
      front-end + AutoGain (see §2); band split validated with 110 Hz/6 kHz/10 kHz tones;
      auto-targets monitor of default sink (`--source` to override). Run:
      `cd ~/Projects/milk-pi && uv run sender.py` (defaults to 192.168.2.108:5005).
- [x] **End-to-end on the panel with real music — working (June 2026), first try**
- [x] Real .milk presets — dotmilk parser/transpiler/runtime + triage (see §4)
- [ ] Packet v1 — only if the v0 layout ever needs changing (wave stereo/length)
- [ ] (shelved) Latency hardening — worked plan in §7
