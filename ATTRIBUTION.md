# Attribution

RayGLow is its own project, built on the shoulders of several others. It is licensed
**MIT** (see [`LICENSE`](LICENSE)); the only code carried in from elsewhere is the
firmware port of `hub75-pio-rs`, whose MIT notice travels with it in
[`firmware/THIRD-PARTY.md`](firmware/THIRD-PARTY.md). Everything below is credited with
thanks.

## MilkDrop — ported DSP front-end

The desktop sender's audio analysis (`sender/sender.py`) is a modified port of MilkDrop's
sound analysis: the FFT front-end (`vis_milk2/fft.cpp`), the band split and per-band
auto-gain (`vis_milk2/plugin.cpp`). MilkDrop's auto-gain output semantics — each band
normalized by its own running average, so "1.0 = typical for this song right now" — are
the interface every RayGLow shader is calibrated to. MilkDrop is no longer the renderer
(the GLSL pipeline replaced it), but it remains the provenance of the DSP and the mental
model.

- MilkDrop3 (the studied source): https://github.com/milkdrop2077/MilkDrop3
- MilkDrop is the work of Ryan Geiss and contributors.

## Shadertoy — compatibility surface

`rayglow/render` implements the Shadertoy shader interface so that shaders written
for shadertoy.com run unmodified: the standard uniforms (`iResolution`, `iTime`,
`iChannel0..3`, `iMouse`, `iDate`, …), the `mainImage(out vec4, in vec2)` entry point,
the 512×2 `audio` channel texture layout, and the multipass Buffer A–D convention.

This is a compatibility surface, not derived code: GLSL ES 3.0 is a Khronos standard, and
RayGLow's EGL/VideoCore/readback/hot-reload plumbing is original. Shaders in
`rayglow/render/presets/` adapted from shadertoy.com remain the property of their original
authors and are used here as test material and learning references.

- Shadertoy: https://www.shadertoy.com

## kjagiello/hub75-pio-rs — firmware architecture base (MIT)

The RP2350 firmware (`firmware/`) is a port of Krzysztof Jagiello's `hub75-pio-rs`
from `rp2040-hal` to `rp235x-hal`: its zero-CPU 3-PIO-state-machine + 4-DMA scan-out
engine, the DMA register access, and the CIE/gamma LUT carry over largely unchanged
(`firmware/src/{lib,dma,lut}.rs`). RayGLow's structural change is widening the data path
from one HUB75 chain to two parallel chains for a 256×64 wall.

- https://github.com/kjagiello/hub75-pio-rs
- License: **MIT**. As ported code, its copyright notice is retained in
  [`firmware/THIRD-PARTY.md`](firmware/THIRD-PARTY.md).

## pitschu/RP2040matrix-v2 — bulk-repack reference

The firmware's bulk frame → bit-plane repack pattern (the fast path that replaces
per-pixel drawing) was informed by pitschu's two-channel repack approach. No code was
copied — only the algorithm/idea was studied — so this is a thanks, not a license
obligation. The upstream carries a non-commercial "further use requires consent" notice;
RayGLow neither vendors nor redistributes any of it.

- https://github.com/pitschu/RP2040matrix-v2 — also https://pitschu.de

## hzeller/rpi-rgb-led-matrix — origin & inspiration (GPL-2.0)

RayGLow started life driving the panels directly from a Raspberry Pi with Henner Zeller's
`rpi-rgb-led-matrix` C++/Cython library, and its pixel-mapper / bit-plane math was
background reading for the firmware. That direct-drive output path has since been retired
in favor of the RP2350 SPI link, so RayGLow no longer depends on, links, or vendors any
of it. Thanks to hzeller and contributors for the library that made the first version
possible.

- https://github.com/hzeller/rpi-rgb-led-matrix
- License: GPL-2.0-or-later. Because none of it is present in or required by RayGLow,
  there is no copyleft reach into this project — hence the permissive MIT license is clean.

## Tools / standards also relied on

NumPy, Pillow, PortAudio via `sounddevice`, PipeWire/Pulse, the GLSL ES / EGL / OpenGL ES
specifications (renderer); Rust + `rp235x-hal` + `defmt` + `probe-rs` (firmware); KiCad +
SKiDL (hardware).
