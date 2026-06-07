"""Shadertoy-compatible GLSL renderer for the LED matrix.

Paste a shader from shadertoy.com into a .glsl file unchanged (it must define
``mainImage(out vec4 fragColor, in vec2 fragCoord)``) and run it on the panel:

    sudo ~/rgbvenv/bin/python -m shadertoy ../example.glsl

The shader runs on the VideoCore VI GPU via a headless (surfaceless) EGL +
OpenGL ES 3 context — no X server — while the hzeller GPIO thread keeps
bit-banging the matrix on core 3.  See __main__.py for the CLI.

Keep this module light: no GL or numpy imports at package-import time.
"""
