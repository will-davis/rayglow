# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in
this repository.

## Orientation

RayGLow renders audio-reactive GLSL on a 256×32 HUB75 LED matrix. **Read `README.md`
first** — it has the architecture diagram, the two-machine split, and the repo map. Two
halves live here and are kept in sync by git, not file-copy:

- **`sender/`** — the desktop daemon (`sender.py`): captures audio, sends feature
  packets. Standalone uv project (numpy + sounddevice). Has its own `README.md` +
  `CLAUDE.md`; the MilkDrop-DSP-port invariants live there.
- **`rayglow/`** — the Pi package, installed editable into the Pi's `~/rgbvenv`:
  - `rayglow/feed/` — the audio-feature feed (packet `receiver`, `FeatureState`,
    rig `config`). Neutral, shared; the future yaml-config target.
  - `rayglow/render/` — **the live renderer**: headless EGL + GLES3 on the Pi's
    VideoCore VI, running Shadertoy-dialect shaders. Entry: `python -m rayglow.render`.
  - `rayglow/legacy/` — the **retired** MilkDrop-faithful NumPy/OpenCV renderer +
    NS-EEL transpiler. Still runs (imports `rayglow.feed`); not maintained.
  - `rayglow/fake_sender.py` — music-free test harness, same packet struct.
- **`experiments/`** — standalone matrix sketches, no `rayglow` import.
- **`docs/design-history/`** — superseded design docs kept for provenance.

## What this is (and isn't)

RayGLow is **its own project**, not a fork. MilkDrop = a ported DSP front-end (its
auto-gain semantics are now the project's protocol); Shadertoy = a compatibility surface
the renderer implements so site shaders run unchanged; hzeller's `rgbmatrix` = the
runtime dependency. See `ATTRIBUTION.md`. Don't reintroduce "milkdrop"/"shadertoy" as
identities — but note the strings survive legitimately in two places: the packet magic
`MILK`/`0x4D494C4B`, and the `milk` / `audio` iChannel **spec names** in shaders. Those
are wire/shader-facing names, not package names — don't rename them.

## Invariants that look like bugs but aren't

- **The packet is a cross-machine contract.** `sender/sender.py`'s `PACKET_FMT` (564 B,
  v1) and `rayglow/feed/receiver.py` must change in lockstep and bump `VERSION`. The
  receiver accepts v0 (556 B) + v1, substituting `sub = bass` for v0. The full
  rules-that-look-wrong list (linear band thirds, equalize-on, the deliberately
  inconsistent `analyze_sub`, deferred `sounddevice` import) is in `sender/CLAUDE.md`.
- **`rgbmatrix` is NOT a dependency in `pyproject.toml`.** It only builds on the Pi
  (Cython + GPIO); listing it breaks install on the desktop. It's prebuilt in the Pi's
  `~/rgbvenv` and imported lazily (`feed.config.matrix_options()`, `render` hardware
  path) so headless/dry-run never touches it — keep those imports lazy.
- **Import direction:** the live path (`render`) and `legacy` both import *up* into
  `feed`; nothing imports *into* `legacy`. Keep `feed` dependency-free of the renderers.
- **Deploy is editable-install, not PYTHONPATH:** `uv pip install --python
  ~/rgbvenv/bin/python -e ~/rayglow` (rgbvenv is uv-managed and has no `pip` of its own).
  `sudo` scrubs env (so PYTHONPATH would need `-E`) but respects the installed package.
  Hardware mode keeps root for GPIO + to re-read `.glsl` on hot reload, so the clone must
  live where root can read it (under `~`).

## Working across the two machines

The Pi has its own long-lived agent context with deep build history; this repo (+ git
history + `docs/design-history/`) is the durable shared memory between machines —
prefer writing knowledge into tracked files over relying on either agent's context. The
Pi deploys by `git pull`; the sshfs mount at `~/local-mount/rpi4/` is only a convenience
for live-editing shaders.

## Verifying changes

- Renderer numerics, no hardware: `python -m rayglow.render <shader> --dry-run 120
  --no-listen` → frame stats + a GIF (works on the desktop's EGL too).
- Sender: `cd sender && uv run sender.py --debug` → 1 Hz status line.
- On the panel: `sudo ~/rgbvenv/bin/python -m rayglow.render
  rayglow/render/presets/milk-verbose.glsl` with the sender running — the reference card
  reacts to audio.

No test suite or linter. Validation is empirical (sine tones, dry-run GIFs, the panel).
