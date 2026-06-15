// 02 — DRAWING A LINE
// =============================================================================
// "Drawing a line" is the same move as the circle: pick a distance field that
// is ZERO on the line and grows as you move away, then make small distances
// bright. Three flavors, easiest to most general.
//
// 1. A HORIZONTAL LINE -------------------------------------------------------
//    The line y = 0 is exactly the set where p.y = 0. Distance to it is just
//    abs(p.y). So abs(p.y) is a distance field for that line, and a thin bright
//    band around it is a drawn line. (Compare to file 01's ring: same idea —
//    threshold the abs of a signed quantity.)
//
// 2. A CURVE y = f(x) --------------------------------------------------------
//    Generalize: how far is the pixel, vertically, from the curve y = f(x)?
//    That's abs(p.y - f(x)). Thresholding it draws the graph of f. This is the
//    "plot a function" trick — here f is a traveling sine wave, animated with
//    iTime. Caveat worth knowing: abs(p.y - f(x)) measures VERTICAL distance,
//    not true perpendicular distance, so a steep curve looks thinner than a
//    flat one. For thin glowing curves nobody notices; for thick precise
//    strokes you'd want the real distance (see #3).
//
// 3. A SEGMENT (TRUE DISTANCE) -----------------------------------------------
//    The honest "distance from point p to the segment from a to b" is a tiny
//    classic (due to Inigo Quilez). Project p onto the infinite line through
//    a,b, CLAMP the projection to stay on the segment, and measure to that
//    nearest point:
//
//        h = clamp( dot(p-a, b-a) / dot(b-a, b-a), 0.0, 1.0 );  // 0..1 along
//        dist = length( (p-a) - (b-a)*h );
//
//    h is "how far along the segment the nearest point is" (0 = at a, 1 = at
//    b, clamped so the ends are rounded caps). This returns genuine Euclidean
//    distance, so the stroke has uniform thickness in every direction. It's
//    also your first real 2D SDF — file 03 makes the "signed" part explicit.
//
// TWO WAYS TO INK A DISTANCE -------------------------------------------------
//   * crisp stroke:  1.0 - smoothstep(0.0, w, dist)     (thickness w, flat top)
//   * soft GLOW:     w / dist  (or w/dist^2)            (bright core, long
//     falloff — cheap neon; clamp/accumulate so it doesn't blow out). Both
//     appear below.
//
// Run: ...render tutorial/02-the-line.glsl --dry-run 90 --no-listen
// =============================================================================

// True point-to-segment distance (keep this in your toolbox).
float sdSegment(vec2 p, vec2 a, vec2 b)
{
    vec2 pa = p - a, ba = b - a;
    float h = clamp(dot(pa, ba) / dot(ba, ba), 0.0, 1.0);
    return length(pa - ba * h);
}

void mainImage(out vec4 fragColor, in vec2 fragCoord)
{
    vec2 p = (fragCoord - 0.5 * iResolution.xy) / iResolution.y;
    vec3 col = vec3(0.0);

    // 1. Horizontal baseline at y = -0.35, as a crisp stroke. distance = how
    //    far p.y is from -0.35.
//    {
//        float d = abs(p.y - (-0.25));
//        col += vec3(0.25, 0.3, 0.4) * (1.0 - smoothstep(0.0, 0.02, d));
//    }

    // 2. A traveling sine curve y = f(x), drawn as a soft glow. f rides at
    //    y=+0.1 with amplitude 0.18; iTime slides the phase so it flows along
    //    the long axis of the panel — which is what the 8:1 shape is good for.
//    {
//        float f = 0.1 + 0.18 * sin(p.x * 2.0 - iTime * 8.0);
//        float d = abs(p.y - f);
//        col += vec3(0.9, cos(iTime) * 2., sin(iTime)) * (0.024 / d);   // 1/d glow
//    }

    // 3. A real segment with true (rounded-cap) thickness, between two points,
    //    drawn as a crisp stroke. Endpoints bob in antiphase so you can watch
    //    the uniform-thickness stroke swing around.
    for (float i = 1.; i <= 12.; i += 1.0) {
        float ipart = i / 5.; 
        vec2 a = vec2(cos(iTime) * ipart, -sin(iTime) * ipart);
        vec2 b = vec2(sin(iTime) * ipart, cos(iTime) * ipart);
        float d = sdSegment(p, a, b);
        col += vec3(1.0, cos(iTime * 4. - i * 0.5), sin(iTime * 4. - i * 0.5)) * (1.0 - smoothstep(0.01, 0.02, d));
    }

    // 3. A real segment with true (rounded-cap) thickness, between two points,
    //    drawn as a crisp stroke. Endpoints bob in antiphase so you can watch
    //    the uniform-thickness stroke swing around.
//    {
//        vec2 a = vec2(-1.2, 0.30 * sin(iTime));
//        vec2 b = vec2( 1.2, -0.30 * sin(iTime));
//        float d = sdSegment(p, a, b);
//        col += vec3(0.3, 0.9, 0.6) * (1.0 - smoothstep(0.06, 0.09, d));
//    }

    fragColor = vec4(col, 1.0);
}
