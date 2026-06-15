// 00 — HELLO, PIXEL
// =============================================================================
// The one idea the whole tutorial is built on. Read this even if it looks
// trivial — every later trick is a corollary of it.
//
//   A fragment shader is a PURE FUNCTION from pixel position to color.
//
// The GPU calls mainImage() once for every pixel — all of them effectively at
// the same time, on hundreds of tiny cores — and hands you that pixel's
// location in `fragCoord`. You do NOT loop over pixels, and you never "draw":
// no setPixel, no lineTo, no canvas you mutate. You answer exactly one
// question, in isolation, with no knowledge of any other pixel:
//
//   "Given THIS location, what color belongs here?"   ->  fragColor
//
// That inversion is the whole mental shift. It means:
//   * "draw a circle"  becomes  "is this point inside the circle?"
//   * "draw a line"    becomes  "how far is this point from the line?"
//   * "draw a sphere"  becomes  "does a ray through this pixel hit a sphere?"
// The craft is choosing a function of position that happens to LOOK like the
// thing you pictured. Everything from here is vocabulary for building such
// functions.
//
// COORDINATES ----------------------------------------------------------------
//   `fragCoord` is in pixels: x in [0, width], y in [0, height], with the
//   origin at the BOTTOM-LEFT and y pointing UP (like math, not like image
//   files). Raw pixels are a bad place to do geometry because their range
//   depends on resolution, so step one of almost every shader is to rescale.
//
//   Two rescalings show up constantly, and you'll see them in every later file:
//
//     uv  = fragCoord / iResolution.xy;                  // -> [0,1] x [0,1]
//     p   = (fragCoord - 0.5*iResolution.xy)/iResolution.y;  // centered, square
//
//   The second one puts (0,0) at the screen center and divides BOTH axes by
//   the HEIGHT. Dividing both by the same number is what keeps a circle a
//   circle instead of an ellipse (this is "aspect correction"). The price is
//   that x is no longer in [-0.5,0.5]: it runs as wide as the panel is wide.
//
//   THIS PANEL IS 256x32 — an extreme 8:1 letterbox. So with the centered
//   coords, p.y runs about [-0.5, 0.5] but p.x runs about [-4, 4]. Picture a
//   canvas 8 units wide and 1 unit tall. Design for it: big shapes, low
//   spatial frequency, high contrast. Anything finer than a few pixels just
//   vanishes on 32 rows of LEDs.
//
//   (Note: the renderer supersamples — `--scale 4` means iResolution is really
//   1024x128 — but the 8:1 RATIO is what matters and it never changes.)
//
// RUN ME ---------------------------------------------------------------------
//   ~/venv/bin/python -m rayglow.render \
//       rayglow/render/presets/tutorial/00-hello-pixel.glsl --dry-run 60 --no-listen
//   then open the GIF it prints (default /tmp/shadertoy_out.gif).
//   On the panel: sudo ~/venv/bin/python -m rayglow.render <same path>
// =============================================================================

void mainImage(out vec4 fragColor, in vec2 fragCoord)
{
    // [0,1] coordinates. Make them visible: put x in the red channel and y in
    // the green channel. The result is a gradient — black at bottom-left
    // (0,0), red toward the right (x->1), green toward the top (y->1), yellow
    // at top-right. This is literally a picture of the coordinate system.
    vec2 uv = fragCoord / iResolution.xy;
    vec3 col = vec3(uv, 0.3);

    // Now the centered, aspect-correct coordinates we'll use from file 01 on.
    // To prove where its origin and axes are, paint the axes bright white:
    // a pixel is "on the y-axis" when its p.x is near 0, "on the x-axis" when
    // p.y is near 0. `abs(p.x) < 0.01` is true in a thin vertical stripe down
    // the middle; step() turns that test into 1.0/0.0, and mix() uses it to
    // pick white. (Note the cross sits dead-center, and because x is so
    // stretched the vertical line is the only one that fills the height.)
    vec2 p = (fragCoord - 0.5 * iResolution.xy) / iResolution.y;
    col = mix(col, vec3(1.0, 1.0, sin(iTime)), step(abs(p.x), 0.01));
    col = mix(col, vec3(1.0), step(abs(p.y), 0.01));

    fragColor = vec4(col, 1.0);
}
