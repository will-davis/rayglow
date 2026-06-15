

https://github.com/user-attachments/assets/fb1bfaf4-e451-43c7-8cc3-830d7c186d91

# RayGLow

Audio-reactive GLSL visuals on an RGB LED matrix. Music plays on a desktop; a small
daemon there reduces it to per-frame audio *features* and unicasts them over UDP to a
Raspberry Pi, which renders Shadertoy-dialect GLSL on its GPU and drives a 256×32 HUB75
panel. Write a shader, paste it in, watch it move to the music.

```
will-desktop (PipeWire)                          raspberry pi 4b (192.168.2.108, IoT VLAN)
┌─────────────────────────────────────────┐      ┌────────────────────────────────────────┐
│ music playback ─▶ sink monitor source   │      │ rayglow.feed.receiver  (latest-wins)   │
│        │                                │ UDP  │        │                               │
│ sender.py: capture ─▶ FFT ─▶ bands      │ ───▶ │ rayglow.feed.features (FeatureState +  │
│ + AutoGain + sub band + waveform        │ 5005 │        │           synth fallback)     │
│ ─▶ 564-byte v1 packet @ ~60 Hz          │      │ rayglow.render  GLSL renderer          │
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

## Why features, not streamed audio

Streaming PCM means reconstructing a continuous signal on the far end: ring buffers,
clock-drift resampling, jitter buffers. Features are **stateless per frame** — a lost or
late packet just means the Pi renders with the previous values, and at 60 Hz one held
frame is invisible. The Pi does zero audio work (it's already loaded bit-banging HUB75),
the wire carries ~34 KB/s, and UDP gets used the way UDP wants to be used. Unicast, not
multicast (one receiver; multicast across the User→IoT VLAN boundary would need IGMP
cooperation for no benefit).

## Hardware
<img width="1360" height="684" alt="image" src="https://github.com/user-attachments/assets/b08ebad3-693b-420d-9779-64ea5058f1cc" />

## Repo layout

| path | what |
|---|---|
| `sender/` | **desktop half** — the feature daemon (`sender.py`), its own uv project (numpy + sounddevice). [sender/README.md](sender/README.md) has the protocol + feature detail. |
| `rayglow/feed/` | the audio-feature feed: packet `receiver`, `FeatureState`, and the rig `config` (geometry/network/gamma). Shared by every renderer; the future yaml-config target. |
| `rayglow/render/` | **the live renderer** — headless EGL + GLES3, multipass pipeline, iChannel textures (`audio`, `milk`, noise, images), hot reload, and `presets/*.glsl`. |
| `rayglow/fake_sender.py` | music-free test harness; emits the same packet struct with synthesized features. |
| `rayglow/legacy/` | the **retired** MilkDrop-faithful NumPy/OpenCV renderer + `.milk`/NS-EEL transpiler. Kept as reference; still runs. [rayglow/legacy/README.md](rayglow/legacy/README.md). |
| `experiments/` | standalone matrix sketches predating the renderer (plasma, boids, fluid sims…). |
| `docs/design-history/` | the original project record (MilkDrop reverse-engineering, v0 spec) + the build-history brain-dump. Superseded by the docs above where they disagree. |

## Quickstart

**Desktop (sender):**
```fish
cd sender
uv run sender.py                  # capture default sink's monitor -> the Pi
uv run sender.py --list-sources   # enumerate pulse sources
uv run sender.py --debug          # + raw pre-normalization band energies
```

**Pi (renderer)** — see [Deploy](#deploy) for first-time setup:
```fish
sudo ~/rgbvenv/bin/python -m rayglow.render rayglow/render/presets/milk-verbose.glsl
~/rgbvenv/bin/python  -m rayglow.render rayglow/render/presets/milk-verbose.glsl --dry-run 120  # headless -> GIF, no root
```
`milk-verbose.glsl` is the live-feed reference card — every feature drawn as a labeled
bar. Run the sender on the desktop at the same time to see it react.

## Deploy

The Pi runs the same git checkout, installed editable into its prebuilt matrix venv:

```fish
# on the Pi, first time:
git clone git@github.com:will-davis/rayglow ~/rayglow
uv pip install --python ~/rgbvenv/bin/python -e ~/rayglow   # makes `rayglow` importable
# thereafter:
cd ~/rayglow && git pull
```

`rgbvenv` is the uv-managed venv where hzeller's `rgbmatrix` is already built (it only
compiles on the Pi — that's why it is *not* a dependency in `pyproject.toml`). The
install goes through `uv pip --python <venv>` because that venv has no `pip` of its own.
Editable install (not `PYTHONPATH`) because `sudo` scrubs the environment but respects
the installed package; hardware mode keeps root for GPIO and to re-read shader files on
hot reload, so the clone must live somewhere root can read (e.g. under `~`). The
sshfs/NFS mount of the Pi is now just a convenience for live-editing shaders — not the
sync mechanism.

## Status

Working end-to-end on the panel with real music. The renderer hot-reloads `.glsl` files
live (edit, save, watch the panel recompile). Next up (not yet built): a microphone
input mode for the sender, and pulling config into a user-editable yaml so the project
runs on someone else's matrix/network by editing one file.
