"""Shadertoy-compatible GLSL renderer for the LED matrix.

Paste a shader from shadertoy.com into a .glsl file unchanged (it must define
``mainImage(out vec4 fragColor, in vec2 fragCoord)``) and run it on the panel:

    sudo ~/venv/bin/python -m rayglow.render ../example.glsl

The shader runs on the Pi's GPU via a headless (surfaceless) EGL + OpenGL ES 3
context — no X server — then each frame is packed and shipped to the RP2350 over
SPI, which drives the panels.  See __main__.py for the CLI.

Keep this module light: no GL or numpy imports at package-import time.
"""
