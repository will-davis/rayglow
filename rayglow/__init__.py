"""RayGLow — audio-reactive GLSL visuals on an RGB LED matrix.

The Pi half of the project: a Shadertoy-dialect GLSL renderer (`rayglow.render`)
driven by an audio-feature feed (`rayglow.feed`) sent over UDP from the desktop
`sender`.  See the top-level README.md for the whole system.

Subpackages:
  rayglow.feed    — the audio-feature protocol: packet receiver, FeatureState,
                    panel/network config.  Shared, renderer-agnostic.
  rayglow.render  — the live renderer: headless EGL + GLES3 on the Pi's
                    VideoCore VI, running shaders pasted from shadertoy.com.
  rayglow.legacy  — the retired MilkDrop-faithful NumPy/OpenCV renderer and
                    .milk transpiler.  Kept as design reference; not maintained.

Keep this module import-light: no GL or numpy at package-import time.
"""
