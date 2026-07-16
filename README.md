# RayGLow

Audio-reactive GLSL visuals on a custom RGB LED wall. Music plays on a desktop; a small
daemon there reduces it to per-frame audio *features* and unicasts them over UDP to a
Raspberry Pi 5, which renders Shadertoy-dialect GLSL on its GPU, packs each frame, and
ships it over a **4-lane parallel bus** to an **RP2350b** microcontroller whose PIO+DMA
engine drives the HUB75 panels with zero-CPU, jitter-free timing. Write a shader, paste
it in, watch it move to the music.

https://github.com/user-attachments/assets/fb1bfaf4-e451-43c7-8cc3-830d7c186d91

```
desktop (optional)                 Raspberry Pi 5                         RP2350b PiZero + HAT
┌────────────────────────┐         ┌────────────────────────────┐         ┌───────────────────────┐
│ music ▶ sink monitor   │  UDP    │ feed.receiver (latest-win) │  4-lane │ phase6: PIO+DMA RX    │
│ sender.py: FFT ▶ bands │ ──────▶ │ render: GLSL ▶ pack        │   bus   │ zero-CPU scan-out     │
│ + flywheels/beat/key   │  :5005  │ (hub75.pack, LINEAR RGB)   │ ──────▶ │ ▶ HUB75 ▶ 256×64 wall │
│ ▶ 2996-B v3 @ ~60 Hz   │ ~180KB/s│ headless EGL + GLES3       │  64 KB  │ (2 chains × 4 panels) │
└────────────────────────┘         └────────────────────────────┘         └───────────────────────┘
        point $RAYGLOW_HOST ──────────────▶ your Pi
```

The three stages live in **one repo**. The desktop and Pi halves are Python: the desktop
runs `sender/`, the Pi runs the `rayglow` package installed editable (`pip install -e`)
from a copy of this repo — get it there however you like (`git pull`, or a continuous
file sync such as mutagen/sshfs for live shader editing). The RP2350b half is Rust
firmware (`firmware/`) flashed onto the board, plus a custom level-shifting HAT
(`hardware/`).
First-time setup is in [Deploy](#deploy); full credits and licensing are in
[ATTRIBUTION.md](ATTRIBUTION.md) (RayGLow is **MIT** — see [LICENSE](LICENSE)).

The desktop sender is **optional**: `rayglow.fake_sender` synthesizes the same packet
struct, so the Pi can drive the wall with no music source at all.

## Why features, not streamed audio

Streaming PCM means reconstructing a continuous signal on the far end: ring buffers,
clock-drift resampling, jitter buffers. Features are **stateless per frame** — a lost or
late packet just means the Pi renders with the previous values, and at 60 Hz one held
frame is invisible. The Pi does zero audio work, the wire carries ~0.25 MB/s, and UDP
gets used the way UDP wants to be used.

## Why offload scan-out to the RP2350b

Bit-banging HUB75 from a Linux SoC fights the scheduler — it needs a dedicated RT core
and still jitters (the Pi 5's RP1 southbridge made it worse). The RP2350b runs the refresh
loop entirely in **3 PIO state machines + 4 self-chaining DMA channels**, with the CPU
never touching the pixel path. The Pi renders at whatever rate it likes and ships 64 KB
frames over the link — already gamma-corrected on the Pi (the GPU resolve pass bakes
the firmware `lut.rs` CIE curve into each frame; `--readback legacy` instead applies
the packer's bit-exact LUT replica to a LINEAR readback); the RP2350b holds a
rock-steady, flicker-free refresh.

## Why a parallel bus, not SPI

The link was originally 1-lane SPI, and SPI still works as the proven fallback
(`--transport spi`). But the 64 KB frame takes ~13 ms at 40 MHz SPI — long enough to sit
on the critical path. A **4-lane source-synchronous parallel bus**, clocked out by the
Pi 5's RP1 PIO block, drops that to ~2 ms (clkdiv 3; ~1.3 ms at clkdiv 2). Only the wire
changes: the byte stream is identical to the SPI path (same `hub75.py` packer output,
same READY handshake), so the firmware and packer don't change. (4 lanes, not 8: the RP2350b scan-out engine owns
GP0–18, leaving GP19–27 for the link.) See
[`rayglow/render/piobridge/README.md`](rayglow/render/piobridge/README.md).

## Hardware

<img width="1360" height="684" alt="RayGLow custom HAT" src="https://github.com/user-attachments/assets/b08ebad3-693b-420d-9779-64ea5058f1cc" />

A **Waveshare RP2350-PiZero** on a custom HAT that level-shifts (3.3 V → 5 V via
`SN74AHCT245`) and breaks out two HUB75 chains + the Pi↔RP2350b link. Design files, the
KiCad project, and fab Gerbers are in [`hardware/`](hardware/).

## Repo layout

| path | what |
|---|---|
| `sender/` | **desktop half** — the feature daemon (`sender.py`), its own uv project (numpy + sounddevice). [sender/README.md](sender/README.md) has the protocol + feature detail. |
| `rayglow/feed/` | the audio-feature feed: packet `receiver`, `FeatureState`, and the rig `config` (geometry/network/gamma). Shared, dependency-free. |
| `rayglow/render/` | **the live renderer** — headless EGL + GLES3, multipass pipeline, iChannel textures (`audio`, `milk`, noise, images), hot reload, a TCP **control plane** (`control.py`: push/switch shaders + media controls), the GPU resolve pass + zero-copy dma-heap readback (`output.py`, `dmabuf.py`), the frame packer (`hub75.py`) + link backends (`spi_out.py`, `pio_out.py` / `piobridge/`), and `presets/*.glsl`. |
| `rayglow/fake_sender.py` | music-free test harness; emits the same packet struct with synthesized features. |
| `rayglow/spi_test.py` | static SPI test pattern (no GL) — isolates the link/firmware from the renderer. |
| `firmware/` | **RP2350b Rust firmware** — zero-CPU PIO+DMA HUB75 scan-out, brought up in verifiable phases. [firmware/README.md](firmware/README.md). |
| `hardware/` | **custom HAT** — KiCad project, Gerbers, and the locked net/pinout spec. [hardware/README.md](hardware/README.md). |
| `tools/` | `verify.py` — proves the Python packer (`render/hub75.py`) is byte-identical to the firmware via a Rust golden frame. `dmabuf_probe.py` — the readback benchmark. |
| `ROADMAP.md` | queued workstreams — the living handoff doc between working sessions. |
| `docs/design-history/` | the original project record (MilkDrop reverse-engineering, the RP2350 plan, the build-history brain-dump). Superseded by the docs above where they disagree. |

## Quickstart

**Desktop (sender, optional):**
```fish
cd sender
set -x RAYGLOW_HOST 192.168.0.50   # your Pi's IP (or pass --host); see LOCAL-SETUP.example.md
uv run sender.py                   # capture default sink's monitor -> the Pi
uv run sender.py --list-sources    # enumerate pulse sources
uv run sender.py --debug           # + raw pre-normalization band energies
```

**Pi (renderer)** — see [Deploy](#deploy) for first-time setup:
```fish
# headless, no root, no hardware: render -> animated GIF (works on a desktop GPU too)
python -m rayglow.render rayglow/render/presets/milk-verbose.glsl --dry-run 120 --no-listen

# on the Pi, driving the panels (root for GPIO):
sudo ~/venv/bin/python -m rayglow.render rayglow/render/presets/milk-verbose.glsl
```
`milk-verbose.glsl` is the live-feed reference card — every audio feature drawn as a
labeled bar. Run the sender on the desktop at the same time to see it react.

## Deploy

**Renderer (Raspberry Pi 5).** The Pi runs the same git checkout, installed editable into
a uv venv:

```fish
# on the Pi, first time:
git clone <your-fork-url> ~/rayglow
cd ~/rayglow
uv venv ~/venv
uv pip install --python ~/venv/bin/python -e '.[pi]'   # core + Pi link deps
# thereafter: update the checkout (git pull, or let your file sync — e.g. a
# mutagen session pushing from the dev machine — keep it current)
```

`.[pi]` pulls the Pi-only deps (`spidev`, `gpiozero`, `lgpio`); EGL/GLES come from system
libraries, not pip. Editable install (not `PYTHONPATH`) because `sudo` scrubs the
environment but respects the installed package; hardware mode keeps root for GPIO and to
re-read shader files on hot reload, so the clone must live somewhere root can read (e.g.
under `~`).

- **Parallel link (default, `--transport pio`):** build `piobridge/libpioshim.so`
  against the RP1 `piolib` first — see
  [`rayglow/render/piobridge/README.md`](rayglow/render/piobridge/README.md).
- **SPI fallback (`--transport spi`):** enable SPI and make sure
  `/sys/module/spidev/parameters/bufsiz >= 65536` so a 64 KB frame goes in one transfer.

Per-machine addresses/paths: see [`LOCAL-SETUP.example.md`](LOCAL-SETUP.example.md).

**Firmware (RP2350b).** Flash the board once: `phase6-parallel --features two-chain`
for the 4-lane bus (production) or `phase5-spi` for the SPI fallback. Toolchain
bootstrap and `cargo run`/`probe-rs` instructions are in
[`firmware/README.md`](firmware/README.md).

## Status

Working end-to-end: the desktop sender feeds the renderer, which packs frames the
firmware accepts byte-for-byte (`tools/verify.py` is green). The renderer hot-reloads
`.glsl` files live (edit, save, watch the panel recompile); for the fast dev loop a TCP
control plane (`tools/rayglow_ctl.py push`, or the `tools/nvim-rayglow.lua` save-hook)
ships edits straight to the running renderer in <100ms — skipping the file-sync lag —
and adds media controls (next/prev/play/pause/loop) plus live per-shader supersample
scale (`// rayglow: scale=N` directive or `rayglow-ctl scale`). The Phase 6 4-lane parallel
bus is the production transport (hardware-verified on the custom HAT, driving the full
256×64 wall); the Phase 5 SPI link remains the proven fallback. A microphone input path
exists (`sender/esp32-mic/` + `sender/espnow-dongle/`). Next: pulling config into a
user-editable yaml so the project runs on someone else's wall/network by editing one
file.
