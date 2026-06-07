# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

The **desktop half of Project Milk-Pi**: a feature daemon (`sender.py`, single file) that
captures the PipeWire monitor of the default sink, runs MilkDrop3's exact sound analysis,
and unicasts feature packets over UDP at ~60 Hz to a Raspberry Pi 4 (192.168.2.108:5005)
driving a 256×32 HUB75 LED matrix. **README.md here is the current doc** — read it first:
pipeline diagram, v1 packet table, feature semantics, how features surface in shaders.

The renderer on the Pi is `shadertoy/` — Shadertoy-compatible GLSL on the VideoCore VI
(headless EGL+GLES3). The original MilkDrop-faithful renderer (`milk/`) is retired, but
still owns the shared infra the packets land in: `milk/receiver.py` (the other end of the
contract) and `milk/features.py` (FeatureState + synth fallback). Pi code is mounted on
this desktop at `~/local-mount/rpi4/will-rpi-custom/`; the Pi keeps a synced copy of
`sender.py` there — keep it in sync after edits. `~/Projects/MilkDrop3/project-milk-pi.md`
is the historical record (MilkDrop reverse-engineering, v0 spec, retired renderer) — good
background, but superseded where it disagrees with README.md.

## Commands

```fish
uv run sender.py                  # capture default sink's monitor, send to the Pi
uv run sender.py --list-sources   # enumerate pulse sources
uv run sender.py --debug          # adds raw pre-normalization band energies to the 1 Hz status line
uv run sender.py --host X --port N --fps N --source NAME
```

uv project (Python ≥3.13, numpy + sounddevice). No tests, no linter. Validation is
empirical: sine tones for band placement (110 Hz → bass, 6 kHz → mid, 10 kHz → treb) and
watching the panel. `sender.py.bak` is the pre-sub-band (v0) version.

## Architecture and invariants

`sender.py` is a faithful port of MilkDrop3's analysis (`vis_milk2/fft.cpp` +
`plugin.cpp:8736/8750` in `~/Projects/MilkDrop3/code/`). Rules that look like bugs but
aren't:

- **Replicate the code, not the comments.** MilkDrop's fft.cpp comments recommend octave
  bands; the actual code uses three equal *linear* thirds of the bottom half-spectrum
  (bins [0:85], [85:170], [170:256]). Equalize is ON (a `-1` arg lands on a bool).
  Source line references are cited inline throughout — keep them accurate when editing.
- **The packet is a two-repo contract.** `PACKET_FMT` (564 bytes, v1) must match
  `milk/receiver.py` on the Pi, which asserts the sizes at import and accepts both v0
  (556 bytes) and v1 (= v0 + `(sub, sub_att)`, falling back `sub = bass` for v0). Any
  layout change must be made in lockstep on the Pi and bump `VERSION`. Downstream of the
  receiver, shaders consume the bands via the `milk`/`audio` iChannel textures
  (`shadertoy/textures.py`); the texel map there is calibrated to AutoGain's
  "1.0 = typical" semantics.
- **The `sub` band is intentionally non-MilkDrop.** MilkDrop's "bass" is 0–4 kHz with a
  log-equalize that suppresses the lowest bins ~90×, so subwoofer content is invisible in
  it. `analyze_sub()` deliberately uses a longer window (2048) and *no* equalize. Don't
  "fix" the inconsistency between `analyze()` and `analyze_sub()`.
- **`import sounddevice` is deferred into `main()`** because `PULSE_SOURCE` must be set
  in the environment before PortAudio initializes — that env var is how the ALSA "pulse"
  device is pointed at the monitor source. Don't hoist it to module level.
- **AutoGain semantics** (ported from plugin.cpp:8750): bands are normalized by their own
  running average so values hover ~1.0 regardless of genre/volume — presets on the Pi
  depend on this. Decay rates are FPS-corrected via `adjust_rate_to_fps` (30 fps
  reference).
- The render loop is wall-clock paced (`next_tick += 1/fps`), latest-wins on the receiver
  side; never block sending on capture state.
