# Attribution

RayGLow is an original project. It is not a fork or a derivative of MilkDrop or
Shadertoy — but it stands on ideas, interfaces, and one library from others, credited
here. (No license is declared for RayGLow itself yet; see the note at the end.)

## MilkDrop — ported DSP front-end & conceptual inspiration

The desktop sender's audio analysis (`sender/sender.py`) is a faithful, line-cited port
of MilkDrop's sound analysis: the FFT front-end (`vis_milk2/fft.cpp`), the band split
and per-band auto-gain (`vis_milk2/plugin.cpp`). MilkDrop's **auto-gain output
semantics** — each band normalized by its own running average, so "1.0 = typical for
this song right now" — are the interface every RayGLow shader is calibrated to.

The retired `rayglow/legacy/` renderer additionally reimplements MilkDrop's
feedback-buffer visual model (warp/decay/draw) and includes a from-scratch NS-EEL →
NumPy transpiler for `.milk` presets.

- MilkDrop3 (the studied source): https://github.com/milkdrop2077/MilkDrop3
- MilkDrop is the work of Ryan Geiss and contributors.
- The `.milk` preset files under `rayglow/legacy/milkpresets/dotmilk-presets/` are
  community-authored presets, retained for testing the transpiler; their respective
  authors are named in the filenames and preset metadata.

RayGLow's *visuals* are not MilkDrop's — the live renderer is GLSL, not the MilkDrop
core. MilkDrop is the provenance of the DSP and the mental model, not the output.

## Shadertoy — compatibility surface

`rayglow/render` implements the **Shadertoy shader interface** so that shaders written
for shadertoy.com run unmodified: the standard uniforms (`iResolution`, `iTime`,
`iChannel0..3`, `iMouse`, `iDate`, …), the `mainImage(out vec4, in vec2)` entry point,
the 512×2 `audio` channel texture layout, and the multipass Buffer A–D convention.

This is a compatibility surface, not derived code: GLSL ES 3.0 is a Khronos standard,
and RayGLow's EGL/VideoCore/readback/hot-reload plumbing is original. Shaders in
`rayglow/render/presets/` adapted from shadertoy.com remain the property of their
original authors and are used here as test material and learning references.

- Shadertoy: https://www.shadertoy.com

## hzeller/rpi-rgb-led-matrix — runtime dependency (GPL-2.0-or-later)

The panel is driven by Christoph Friedrich's packaging of Henner Zeller's
`rpi-rgb-led-matrix` C++/Cython library, imported at runtime on the Pi as `rgbmatrix`.
RayGLow **uses** it (does not vendor or modify it); it is installed separately in the
Pi's `rgbvenv`, which is why it is not a dependency in `pyproject.toml`.

- https://github.com/hzeller/rpi-rgb-led-matrix
- License: **GPL-2.0-or-later**. This is the key licensing constraint to resolve before
  any public RayGLow release — importing a GPL library at runtime has implications a
  permissive RayGLow license would need to account for. **TODO (Will): choose RayGLow's
  LICENSE with this in mind.**

## Tools / standards also relied on

NumPy, Pillow, OpenCV (legacy renderer), PortAudio via `sounddevice`, PipeWire/Pulse,
and the GLSL ES / EGL / OpenGL ES specifications.
