# Shader tutorial — from a pixel to a raymarched scene

A hands-on path for building GLSL intuition on the RayGLow panel. Each file is a
complete, runnable shader whose **comments are the lesson** — open the `.glsl`
and read top-to-bottom, then run it and tweak the numbers. Audio is left out on
purpose; this is about *seeing a thing in your head and knowing the function
that draws it*.

Read them in order — each introduces exactly one new idea and reuses everything
before it.

| # | file | the one new idea |
|---|------|------------------|
| 00 | `00-hello-pixel.glsl` | a shader is a **pure function from pixel position to color**; the coordinate systems; this panel's 8:1 shape |
| 01 | `01-distance-and-shapes.glsl` | distance → brightness; `step` vs `smoothstep` (anti-aliasing); disc and ring |
| 02 | `02-the-line.glsl` | "drawing a line" = thresholding distance-to-a-line; the segment SDF |
| 03 | `03-sdf-2d-and-ops.glsl` | **signed** distance + the sign convention; *seeing the field*; union/intersection/subtract; `smin` |
| 04 | `04-transforms-repeat-color.glsl` | transform the coordinates not the shape; infinite repetition; the IQ cosine palette + motion |
| 05 | `05-raymarch-intro.glsl` | the leap to 3D: rays, sphere-tracing, the march loop |
| 06 | `06-raymarch-lighting.glsl` | SDF-gradient normals + diffuse light + a real camera — the 3D payoff |

## Running them

Headless (no panel, no root) — renders a GIF you can open:

```fish
~/venv/bin/python -m rayglow.render \
    rayglow/render/presets/tutorial/00-hello-pixel.glsl --dry-run 120 --no-listen
# -> writes /tmp/shadertoy_out.gif  (override with --out)
```

On the panel — and the best way to learn is to **leave it running and edit**:
the renderer hot-reloads on save, so change a constant, save, watch the LEDs
recompile.

```fish
sudo ~/venv/bin/python -m rayglow.render \
    rayglow/render/presets/tutorial/03-sdf-2d-and-ops.glsl
```

## The single most important habit

When a shader doesn't look the way you pictured, **stop drawing the shape and
draw the field** — assign `d` to a color everywhere instead of thresholding it
(file 03 is built entirely around this). Being able to *see* your distance
function is what turns SDF work from guess-and-check into something you reason
about.

## Designing for *this* panel

256×32 is an extreme letterbox. With the centered coords used throughout
(`p = (fragCoord - 0.5*iResolution.xy)/iResolution.y`), the visible world is
about **8 units wide and 1 unit tall** (`p.x ∈ [-4,4]`, `p.y ∈ [-0.5,0.5]`).
Consequences worth internalizing:

- **Big shapes, low spatial frequency.** Anything finer than a few of the 32
  rows disappears. Contour/stripe frequencies that look great at 1080p alias to
  mush here — keep them low (see the `cos(d * 80.0)` note in file 03).
- **Use the long axis.** Scrolling waves (02), tiled rows (04), and orbiting
  cameras (06) all play to the width.
- **Anti-alias everything.** Prefer `smoothstep` over `step`; hard edges land
  between LEDs and stair-step. The renderer supersamples (`--scale 4`) which
  helps, but soft edges in the shader help more.

## Where to go next

- **Steal from the corpus.** The `presets/` folder one level up is dozens of
  real Shadertoy shaders. With files 00–06 under your belt, open any of them
  and you'll recognize the moves: `length()-r`, `smoothstep` edges, palette
  cosines, a `map()` + march loop.
- **The canonical references** (these files lean on his results):
  Inigo Quilez's [2D distance functions](https://iquilezles.org/articles/distfunctions2d/),
  [3D distance functions](https://iquilezles.org/articles/distfunctions/), and
  [smooth minimum](https://iquilezles.org/articles/smin/). The
  [Ray Marching chapter](https://www.shadertoy.com/view/4dSfRc) and "The Book of
  Shaders" are gentle companions.
- **Extend file 06.** It's a minimal but real raymarcher. Add shapes to `map()`
  (combine with `min`/`smin`), add a second light, try soft shadows or ambient
  occlusion, swap the base color for a palette driven by the hit position.
