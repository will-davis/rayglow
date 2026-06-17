

https://github.com/user-attachments/assets/fb1bfaf4-e451-43c7-8cc3-830d7c186d91

# RayGLow

Audio-reactive GLSL visuals on a custom RGB LED wall. Music plays on a desktop; a small
daemon there reduces it to per-frame audio *features* and unicasts them over UDP to a
Raspberry Pi, which renders Shadertoy-dialect GLSL on its GPU, packs each frame, and
ships it over SPI to an **RP2350** microcontroller whose PIO+DMA engine drives the HUB75
panels with zero-CPU, jitter-free timing. Write a shader, paste it in, watch it move to
the music.

https://github.com/user-attachments/assets/fb1bfaf4-e451-43c7-8cc3-830d7c186d91

```
<<<<<<< HEAD
desktop (PipeWire)                 Raspberry Pi 5                       RP2350 + custom HAT
┌──────────────────────┐   UDP    ┌───────────────────────────┐  SPI   ┌───────────────────────┐
│ music ▶ sink monitor│   5005   │ feed.receiver (latest-win)│ 64 KB  │ phase5_spi: PIO+DMA RX│
│ sender.py: FFT ▶    │ ───────▶ │ render: GLSL ▶ pack     │ ─────▶│ zero-CPU scan-out     │
│ bands+AutoGain+sub   │  ~34KB/s │ (hub75.pack, LINEAR RGB)  │ frames │ ▶HUB75 ▶ 256×64 wall│
│ ▶ 564-B v1 @ ~60 Hz │          │ headless EGL + GLES3      │        │ (2 chains × 4 panels) │
└──────────────────────┘          └───────────────────────────┘        └───────────────────────┘
        set $RAYGLOW_HOST ──────────────────▶ your Pi
```

The three stages live in **one repo**. The desktop and Pi halves are Python and stay in
sync by **git** (not file-copy): the desktop runs `sender/`, the Pi runs the `rayglow`
package (deployed by `git pull` + `pip install -e`). The RP2350 half is Rust firmware
(`firmware/`) flashed onto the board, plus a custom level-shifting HAT (`hardware/`).
First-time setup is in [Deploy](#deploy); full credits and licensing are in
[ATTRIBUTION.md](ATTRIBUTION.md) (RayGLow is **MIT** — see [LICENSE](LICENSE)).
=======
will-desktop (PipeWire)                          raspberry pi 4b (192.168.2.108, IoT VLAN)
┌─────────────────────────────────────────┐      ┌────────────────────────────────────────┐
│ music playback ─▶ sink monitor source  │      │ rayglow.feed.receiver  (latest-wins)   │
│        │                                │ UDP  │        │                               │
│ sender.py: capture ─▶ FFT ─▶ bands    │ ───▶│ rayglow.feed.features (FeatureState +  │
│ + AutoGain + sub band + waveform        │ 5005 │        │           synth fallback)     │
│ ─▶ 564-byte v1 packet @ ~60 Hz         │      │ rayglow.render  GLSL renderer          │
└─────────────────────────────────────────┘      │   (headless EGL + GLES3 on VideoCore)  │
                                                 │        │                               │
                                                 │ hzeller rpi-rgb-led-matrix             │
                                                 │   4× 64×32 P6 HUB75 = 256×32           │
                                                 └────────────────────────────────────────┘
```

The two halves live in one repo and are kept in sync by **git**, not file-copy: the
desktop runs `sender/`, the Pi runs the `rayglow` package (deployed by `git pull` +
`pip install -e`). See [Deploy](#deploy).

Full credits and licensing in [ATTRIBUTION.md](ATTRIBUTION.md).
>>>>>>> origin

## Why features, not streamed audio

Streaming PCM means reconstructing a continuous signal on the far end: ring buffers,
clock-drift resampling, jitter buffers. Features are **stateless per frame** — a lost or
late packet just means the Pi renders with the previous values, and at 60 Hz one held
frame is invisible. The Pi does zero audio work, the wire carries ~34 KB/s, and UDP gets
used the way UDP wants to be used.

## Why offload scan-out to the RP2350

Bit-banging HUB75 from a Linux SoC fights the scheduler — it needs a dedicated RT core
and still jitters (the Pi 5's RP1 southbridge made it worse). The RP2350 runs the refresh
loop entirely in **3 PIO state machines + 4 self-chaining DMA channels**, with the CPU
never touching the pixel path. The Pi renders at whatever rate it likes and ships 64 KB
frames over SPI; the RP2350 holds a rock-steady, flicker-free refresh and applies the CIE
gamma LUT downstream (so the Pi's render readback stays LINEAR).

## Hardware

<img width="1360" height="684" alt="image" src="https://github.com/user-attachments/assets/b08ebad3-693b-420d-9779-64ea5058f1cc" />

A **Waveshare RP2350-PiZero** on a custom HAT that level-shifts (3.3 V → 5 V via
`SN74AHCT245`) and breaks out two HUB75 chains + the Pi↔RP2350 SPI link. Design files,
the KiCad project, and fab Gerbers are in [`hardware/`](hardware/).

## Hardware
<img width="1360" height="684" alt="image" src="https://github.com/user-attachments/assets/b08ebad3-693b-420d-9779-64ea5058f1cc" />

## Repo layout

| path | what |
|---|---|
| `sender/` | **desktop half** — the feature daemon (`sender.py`), its own uv project (numpy + sounddevice). [sender/README.md](sender/README.md) has the protocol + feature detail. |
| `rayglow/feed/` | the audio-feature feed: packet `receiver`, `FeatureState`, and the rig `config` (geometry/network/gamma). Shared, dependency-free. |
| `rayglow/render/` | **the live renderer** — headless EGL + GLES3, multipass pipeline, iChannel textures (`audio`, `milk`, noise, images), hot reload, the SPI frame packer (`hub75.py`) + backend (`spi_out.py`), and `presets/*.glsl`. |
| `rayglow/fake_sender.py` | music-free test harness; emits the same packet struct with synthesized features. |
| `rayglow/spi_test.py` | static SPI test pattern (no GL) — isolates the link/firmware from the renderer. |
| `firmware/` | **RP2350 Rust firmware** — zero-CPU PIO+DMA HUB75 scan-out, brought up in verifiable phases. [firmware/README.md](firmware/README.md). |
| `hardware/` | **custom HAT** — KiCad project, Gerbers, and the locked net/pinout spec. [hardware/README.md](hardware/README.md). |
| `tools/` | `verify.py` — proves the Python packer (`render/hub75.py`) is byte-identical to the firmware via a Rust golden frame. |
| `docs/design-history/` | the original project record (MilkDrop reverse-engineering, the RP2350 plan, the build-history brain-dump). Superseded by the docs above where they disagree. |

## Quickstart

**Desktop (sender):**
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

# on the Pi, driving the panels over SPI (root for GPIO):
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
uv pip install --python ~/venv/bin/python -e '.[pi]'   # core + Pi SPI deps
# thereafter:
cd ~/rayglow && git pull
```

`.[pi]` pulls the Pi-only SPI deps (`spidev`, `gpiozero`, `lgpio`); EGL/GLES come from
system libraries, not pip. Editable install (not `PYTHONPATH`) because `sudo` scrubs the
environment but respects the installed package; hardware mode keeps root for GPIO and to
re-read shader files on hot reload, so the clone must live somewhere root can read (e.g.
under `~`). Enable SPI and make sure `/sys/module/spidev/parameters/bufsiz >= 65536` so a
64 KB frame goes in one transfer. Per-machine addresses/paths: see
[`LOCAL-SETUP.example.md`](LOCAL-SETUP.example.md).

**Firmware (RP2350).** Flash the board once with the Phase 5 SPI-link binary — toolchain
bootstrap and `cargo run`/`probe-rs` instructions are in
[`firmware/README.md`](firmware/README.md).

## Status

Working end-to-end: the desktop sender feeds the renderer, which packs frames the
firmware accepts byte-for-byte (`tools/verify.py` is green). The renderer hot-reloads
`.glsl` files live (edit, save, watch the panel recompile). The RP2350 firmware is
hardware-verified through Phase 4 (full 256×64 animation) with the Phase 5 SPI link in
bring-up against the Pi's `render/spi_out.py`; the second panel chain is being populated.
Next: a microphone input mode for the sender, and pulling config into a user-editable
yaml so the project runs on someone else's wall/network by editing one file.
