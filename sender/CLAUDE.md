# CLAUDE.md — sender/

Guidance for Claude Code in the RayGLow **sender** (desktop half). Repo-wide
orientation is in the top-level `../CLAUDE.md`; read this directory's `README.md` first
for the protocol and feature detail.

## What this is

The desktop feature daemon (`sender.py`, single file): captures the PipeWire monitor of
the default sink, runs MilkDrop3's exact sound analysis, and unicasts 564-byte v1
feature packets over UDP at ~60 Hz to the Pi (192.168.0.50:5005). It's a standalone uv
project — it shares *no code* with the `rayglow` package, only the packet contract
mirrored in `rayglow/feed/receiver.py`. The renderer that consumes these packets is
`rayglow.render` (GLSL on the Pi). `docs/design-history/project-milk-pi.md` is the
historical record (MilkDrop reverse-engineering, v0 spec) — background, superseded where
it disagrees with the READMEs.

## Commands

```fish
uv run sender.py                  # capture default sink's monitor, send to the Pi
uv run sender.py --list-sources   # enumerate pulse sources
uv run sender.py --debug          # adds raw pre-normalization band energies to the 1 Hz status line
uv run sender.py --host X --port N --fps N --source NAME
```

uv project (Python ≥3.13, numpy + sounddevice). No tests, no linter. Validation is
empirical: sine tones for band placement (110 Hz → bass, 6 kHz → mid, 10 kHz → treb) and
watching the panel.

## Architecture and invariants

`sender.py` is a faithful port of MilkDrop3's analysis (`vis_milk2/fft.cpp` +
`plugin.cpp:8736/8750`; cross-reference against a local checkout of the MilkDrop3
source — https://github.com/milkdrop2077/MilkDrop3). Rules that look like bugs but
aren't:

- **Replicate the code, not the comments.** MilkDrop's fft.cpp comments recommend octave
  bands; the actual code uses three equal *linear* thirds of the bottom half-spectrum
  (bins [0:85], [85:170], [170:256]). Equalize is ON (a `-1` arg lands on a bool).
  Source line references are cited inline throughout — keep them accurate when editing.
- **The packet is a cross-machine contract.** `PACKET_FMT` (564 bytes, v1) must match
  `rayglow/feed/receiver.py`, which asserts the sizes at import and accepts both v0
  (556 bytes) and v1 (= v0 + `(sub, sub_att)`, falling back `sub = bass` for v0). Any
  layout change must be made in lockstep with the receiver and bump `VERSION`. Downstream
  of the receiver, shaders consume the bands via the `milk`/`audio` iChannel textures
  (`rayglow/render/textures.py`); the texel map there is calibrated to AutoGain's
  "1.0 = typical" semantics. (Note: "milk" survives as the *iChannel spec name* and the
  packet magic — it is not the package name. Don't rename those.)
- **The `sub` band is intentionally non-MilkDrop.** MilkDrop's "bass" is 0–4 kHz with a
  log-equalize that suppresses the lowest bins ~90×, so subwoofer content is invisible in
  it. `analyze_sub()` deliberately uses a longer window (2048) and *no* equalize. Don't
  "fix" the inconsistency between `analyze()` and `analyze_sub()`.
- **`import sounddevice` is deferred into `main()`** because `PULSE_SOURCE` must be set
  in the environment before PortAudio initializes — that env var is how the ALSA "pulse"
  device is pointed at the monitor source. Don't hoist it to module level.
- **AutoGain semantics** (ported from plugin.cpp:8750): bands are normalized by their own
  running average so values hover ~1.0 regardless of genre/volume — shaders on the Pi
  depend on this. Decay rates are FPS-corrected via `adjust_rate_to_fps` (30 fps
  reference).
- The send loop is wall-clock paced (`next_tick += 1/fps`), latest-wins on the receiver
  side; never block sending on capture state.

## Planned (not yet built)

A microphone input mode (alternative capture source alongside the sink monitor) is the
next feature here — keep the capture path factored so a second source slots in cleanly.
