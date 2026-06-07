# milk-pi — desktop feature daemon

The broadcast half of a two-machine audio visualizer. `sender.py` (this repo, one file)
captures whatever the desktop is playing, reduces it to a handful of per-frame audio
features, and unicasts them over UDP at ~60 Hz to a Raspberry Pi 4B driving a 256×32
HUB75 RGB LED matrix. The Pi renders Shadertoy-compatible GLSL on its VideoCore VI GPU,
with the features exposed to shaders as textures.

```
will-desktop (CachyOS, PipeWire)                 raspberry pi 4b (192.168.2.108, IoT VLAN)
┌─────────────────────────────────────────┐      ┌────────────────────────────────────────┐
│ music playback ─▶ sink monitor source   │      │ milk/receiver.py  (latest-wins UDP)    │
│        │                                │ UDP  │        │                               │
│ sender.py: capture ─▶ FFT ─▶ band       │ ───▶ │ milk/features.py  (FeatureState +      │
│ energies + AutoGain + sub band + wave   │ 5005 │        │           synth fallback)     │
│ ─▶ 564-byte v1 packet @ ~60 Hz          │      │ shadertoy/  GLSL renderer              │
└─────────────────────────────────────────┘      │   (headless EGL + GLES3 on VideoCore)  │
                                                 │        │                               │
                                                 │ hzeller rpi-rgb-led-matrix             │
                                                 │   4× 64×32 P6 HUB75 = 256×32           │
                                                 └────────────────────────────────────────┘
```

Pi-side code: `~/rpi-rgb-led-matrix/will-rpi-custom/` on the Pi, mounted on the desktop
at `~/local-mount/rpi4/`. The Pi keeps a synced copy of `sender.py` for reference; the
daemon only ever *runs* on the desktop (it's capturing desktop audio).

## History, and why everything is called "milk"

The project began as a from-scratch port of **MilkDrop** (the Winamp visualizer) to the
Pi: packet magic `MILK`, a NumPy/OpenCV feedback-buffer renderer in `milk/`, and a
`.milk`-preset transpiler. That renderer worked end-to-end — and was then largely
retired when it turned out the Pi 4B's GPU can run real GLSL through a surfaceless EGL
context, no X server, while the CPU keeps bit-banging HUB75. The replacement,
`shadertoy/`, executes shaders pasted unchanged from shadertoy.com and is now the
canvas for original visuals.

Two things survived the pivot and define this repo:

1. **The packet protocol** (`milk/receiver.py` is still the listener, and the
   `FeatureState` it feeds drives the GLSL textures).
2. **MilkDrop's sound analysis**, ported instruction-for-instruction. MilkDrop is no
   longer the destination — it's the *provenance of the DSP*. Its auto-gain output
   semantics (see below) turned out to be the right interface for audio-reactive
   visuals of any kind, and every shader downstream is calibrated to them.

`~/Projects/MilkDrop3/project-milk-pi.md` is the original project record — still the
best reference for the MilkDrop reverse-engineering and the retired `milk/` renderer,
but its §5 packet spec describes v0 and its renderer sections describe the previous
architecture. *This* README is current for the sender and the protocol.

## Why features, not audio

Streaming PCM means reconstructing a continuous signal on the far end: ring buffers,
clock-drift resampling, jitter management. Features are **stateless per frame** — a
lost or late packet just means the Pi renders with the previous values, and at 60 Hz
one held frame is invisible. The Pi does zero audio work (it's already loaded by HUB75
bit-banging), the wire carries ~34 KB/s, and UDP gets used the way UDP wants to be
used. Unicast, not multicast: one receiver, and multicast across the User→IoT VLAN
boundary would need IGMP cooperation from the switch and OPNsense for no benefit.

## Running

```fish
cd ~/Projects/milk-pi
uv run sender.py                  # monitor of default sink -> 192.168.2.108:5005
uv run sender.py --list-sources   # enumerate pulse sources
uv run sender.py --source NAME    # capture a specific source instead
uv run sender.py --debug          # adds raw pre-normalization band energies
uv run sender.py --host H --port N --fps N
```

Prints a 1 Hz status line (`sub/bass/mid/treb` as `imm/att`, plus `vol`). uv project,
Python ≥3.13, numpy + sounddevice; `pactl` must be on PATH. The capture path: the ALSA
"pulse" PortAudio device is a PipeWire/Pulse client, and the `PULSE_SOURCE` environment
variable selects which source it records — which is why `sender.py` defers
`import sounddevice` until after setting it.

On the Pi (see `shadertoy/__main__.py` for the full CLI):

```fish
sudo ~/rgbvenv/bin/python -m shadertoy presets/foo.glsl          # on the panel
~/rgbvenv/bin/python -m shadertoy presets/foo.glsl --dry-run 120 # headless -> GIF
```

## The features

Everything rides on **AutoGain**, ported from MilkDrop (`plugin.cpp:8750`): each band
is divided by its own long-running average. So a band value of **1.0 means "typical
for this song right now"** — quiet passages dip toward ~0.5, hits spike to 2–4 —
regardless of genre, mastering level, or system volume. This is why one shader works
for everything. Each band also has an `_att` twin (temporally smoothed: slow swells
instead of per-frame punch).

| field | what it is |
|---|---|
| `bass`, `mid`, `treb` | MilkDrop's three bands: 576-sample window → Hann → 1024-pt FFT → 512 bins × log-equalize → three equal *linear* thirds of the bottom half ≈ 0–4 / 4–8 / 8–12 kHz at 48 kHz |
| `vol` | sum of the three, own AutoGain |
| `wave[128]` | mono waveform window, downsampled 576 → 128, ±1.0 |
| `sub`, `sub_att` | **v1, not MilkDrop**: true sub-bass. MilkDrop's "bass" covers 0–4 kHz with a log-equalize that suppresses the lowest bins ~90× — a subwoofer is invisible in it. `sub` uses a separate 2048-sample FFT (23.4 Hz/bin), *no* equalize, bins 1–5 = 23–117 Hz, own AutoGain |

The MilkDrop chain is ported from the actual code, not its comments (`fft.cpp` comments
recommend octave bands; the code uses linear thirds — `sender.py` cites the source
lines inline). Band placement was validated with 110 Hz / 6 kHz / 10 kHz sine tones.

## Packet — v1, little-endian, 564 bytes

`PACKET_FMT = "<IHHIf7f128f2f"` in `sender.py`; mirrored (with the size asserted) in
`milk/receiver.py`. **Any layout change must land on both ends and bump `version`.**
The receiver accepts v0 (556 bytes, no sub) and v1, substituting `sub = bass` for v0.

| offset | type | field |
|---|---|---|
| 0 | uint32 | magic = `0x4D494C4B` ("MILK") |
| 4 | uint16 | version = 1 |
| 6 | uint16 | flags (reserved) |
| 8 | uint32 | seq — wraps at 2³²; receiver drops stale/reordered (RFC 1982-style compare) |
| 12 | float32 | t — sender monotonic seconds |
| 16 | float32 | bass |
| 20 | float32 | mid |
| 24 | float32 | treb |
| 28 | float32 | bass_att |
| 32 | float32 | mid_att |
| 36 | float32 | treb_att |
| 40 | float32 | vol |
| 44 | float32[128] | wave |
| 556 | float32 | sub |
| 560 | float32 | sub_att |
| **564** | | **total** |

Receiver discipline (`milk/receiver.py`): bind once, drain the socket nonblocking every
frame keeping only the highest seq, never block the render loop. No packet for 0.5 s →
`FeatureState` switches to a synthesized fallback (bands breathing around 1.0, fake
beat) so the panel never freezes or goes dark.

## How the features reach shaders

`shadertoy/` exposes the feed as `iChannel` textures, bound per-shader with comment
directives (`// iChannel0: milk`) or `--channelN` flags:

- **`milk`** — an 8×1 RGBA32F texture (float: the >1.0 spikes survive; read with
  `texelFetch`). Texels 0–4 are bass/mid/treb/vol/sub; per band `.x` = imm, `.y` = att,
  plus two Pi-derived signals: `.z` = d/dt (signed onset detector) and `.w` = imm
  through a ~125 ms envelope. Texel 5 holds integrated phase per band ("music time":
  `theta += imm·dt`, for seamless `sin(theta·k)` motion), texel 6 the sub phase plus
  feed health (`pkt_age` seconds, `live` 0/1 — gate on it to fade to an ambient mode
  when music stops). Full map: `shadertoy/textures.py` (`MilkChannel`); live
  reference card that draws every float as a labeled bar:
  `shadertoy/presets/milk-verbose.glsl`.
- **`audio`** — the shadertoy.com-faithful 512×2 spectrum/waveform texture, so stock
  shaders work unmodified. The spectrum row is rebuilt Pi-side from `wave[128]`
  (Web-Audio dB scaling, 0.8 smoothing) — faithful to the site, which means heavily
  compressed: bass pins near 1.0 whenever music plays. For dynamic band values use
  `milk`.

Division of labor on derived signals: the **desktop** computes what needs
full-resolution audio (FFTs, band split, AutoGain); the **Pi** computes what only needs
the scalars themselves (d/dt, envelopes, phase integration) in `MilkChannel.update()`,
keeping the wire format small and renderer-agnostic.

## Repo map

| where | what |
|---|---|
| `sender.py` (here) | the daemon — capture, analysis, packets |
| `sender.py.bak` | pre-sub-band v0 version, kept for reference |
| `~/local-mount/rpi4/will-rpi-custom/shadertoy/` | GLSL renderer (EGL bindings, multipass pipeline, channel textures, hot reload, presets) |
| `~/local-mount/rpi4/will-rpi-custom/milk/` | packet receiver + FeatureState + panel config — shared infra; also the retired MilkDrop-faithful renderer and `fake_sender.py` (music-free test harness, same struct) |
| `~/Projects/MilkDrop3/project-milk-pi.md` | historical project record: MilkDrop reverse-engineering, the v0 spec, the retired renderer |
