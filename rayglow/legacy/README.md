# rayglow.legacy — the retired MilkDrop-faithful renderer

**Status: retired. Kept as design reference. Not maintained.** The live renderer
is `rayglow.render` (GLSL on the Pi's GPU). Nothing in the active path imports
from here.

## What it was

The project's first renderer, and for a while the *whole* project: a from-scratch
reimplementation of MilkDrop's visual core in NumPy + OpenCV, running on the Pi's
CPU and driving the 256×32 panel. It worked end-to-end with real music.

MilkDrop is a feedback loop on a framebuffer — an IIR filter on the image:

```
prev_frame → warp (resample through a displacement field) → decay
           → draw audio geometry → next_frame → (composite) → display
```

This package ports that faithfully:

- `engine.py` — the core loop: read feature packet → update steering params →
  `cv2.remap` warp → decay/clamp → draw waveform/shapes → tone-map → matrix out.
- `warp.py` — a per-pixel port of MilkDrop's `milkdropfs.cpp:1698-1837`, including
  the exact drunken-sine warp constants. At LED resolution it runs *truly* per
  pixel (the original's coarse mesh is skipped — 8k px is affordable in vectorized
  NumPy).
- `framebuffer.py` — the float feedback buffer + the load-bearing post-draw clamp
  (MilkDrop's 8-bit feedback texture implicitly clamped every frame; without it,
  waveform pixels equilibrate at brightness/(1−decay)).
- `draw.py` — waveform/shape geometry injection.
- `milkpresets/` — hand-written presets (`tunnel.py`, `wobble.py`) as pure
  functions `FeatureState → steering dict`, mirroring MilkDrop's host/preset split.
- `dotmilk/` — the ambitious part: a real `.milk` parser plus a full **NS-EEL →
  NumPy transpiler** (loops, `megabuf`/`gmem`, `x[i]` memory syntax), a MilkPreset
  runtime (per-frame scalar, per-pixel vectorized, main-wave modes 0–7, custom
  waves/shapes, borders), and a `triage` tool that grades a preset library.
  `milkpresets/dotmilk-presets/` holds ~311 real `.milk` files it can run.

## Why it was retired

The Pi 4B's VideoCore GPU can run real GLSL through a surfaceless EGL context
(no X), while the CPU keeps bit-banging HUB75. That unlocked the entire
shadertoy.com corpus and a far wider visual range than the MilkDrop core, for
less CPU. The renderer pivoted to `rayglow.render`; this stayed as reference.

## It still runs

It imports the live feed from `rayglow.feed` (the one piece both renderers share),
so on the Pi:

```fish
# headless benchmark (no root):
~/rgbvenv/bin/python -m rayglow.legacy --headless --preset tunnel
# on the panel:
sudo ~/rgbvenv/bin/python -m rayglow.legacy --preset tunnel
# real .milk presets via the transpiler:
sudo ~/rgbvenv/bin/python -m rayglow.legacy --milk rayglow/legacy/milkpresets/dotmilk-presets/ --shuffle
```

The MilkDrop reverse-engineering that informed all of this lives in
`docs/design-history/project-milk-pi.md`.
