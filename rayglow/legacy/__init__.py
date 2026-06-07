"""rayglow.legacy — RETIRED MilkDrop-faithful renderer.  Kept as reference.

This was the project's first renderer: a feedback-buffer (IIR-on-a-framebuffer)
core in NumPy/OpenCV that reproduced MilkDrop's warp/decay/draw loop on the
panel, plus a full .milk parser and NS-EEL->NumPy transpiler (`dotmilk/`).  It
worked end-to-end on hardware before the project pivoted to running real GLSL on
the Pi's GPU (see `rayglow.render`).

It is NOT maintained, but it still runs — it imports the live audio feed from
`rayglow.feed` (config / FeatureState / Receiver), the one piece both renderers
share.  Kept because the warp port and the EEL transpiler are substantial work
and a useful design reference.  See legacy/README.md for what it was and how to
run it.

Engine (this package) is preset-agnostic; presets (`legacy/milkpresets/`) are
pure functions FeatureState -> steering dict, mirroring MilkDrop's host/preset
split.

Run (on the Pi):
  headless benchmark (no root):  ~/rgbvenv/bin/python -m rayglow.legacy --headless --preset tunnel
  hardware:                      sudo ~/rgbvenv/bin/python -m rayglow.legacy --preset tunnel
"""
