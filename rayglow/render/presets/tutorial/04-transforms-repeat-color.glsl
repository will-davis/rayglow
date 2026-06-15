// 04 — MOVING, ROTATING, REPEATING, AND COLORING
// =============================================================================
// You can already build shapes; now make them move, tile across the long panel,
// and not be monochrome. Three reusable ideas.
//
// 1. TRANSFORM THE COORDINATES, NOT THE SHAPE -------------------------------
//    There's no shape object to move — there's only the point p you're asked
//    about. So to move/rotate/scale a shape, apply the INVERSE transform to p
//    before you evaluate the SDF. It feels backwards and it is, deliberately:
//
//        translate by t:   evaluate sdf(p - t)     (you saw this in file 01)
//        rotate by angle a: evaluate sdf( rot(-a) * p ), and since a rotation
//                           matrix's inverse is its transpose, people just
//                           write rot(a)*p and let the sign ride.
//        scale by s:        evaluate sdf(p / s) * s  (the trailing *s keeps the
//                           field a true distance — important for raymarching).
//
//    A 2x2 rotation matrix:  [ c -s ]   applied as rot(a) * p.
//                            [ s  c ]
//
// 2. INFINITE REPETITION WITH ONE LINE --------------------------------------
//    To tile a shape every `c` units along an axis, map every point back into
//    a single cell centered on 0 before evaluating:
//
//        p.x = p.x - c * round(p.x / c);     // wrap x into [-c/2, c/2]
//
//    One SDF call now paints infinitely many copies — you compute one cell and
//    space does the duplication for free. This is MADE for an 8-wide canvas.
//    Keep the cell id (which copy you're in) to vary each tile:
//
//        float id = round(p.x / c);          // ..., -1, 0, 1, ... per column
//
//    (round() is fine in GLSL ES 3.00; floor(x+0.5) is the same thing if you
//    ever need a fallback.)
//
// 3. COLOR FROM A COSINE PALETTE --------------------------------------------
//    The standard way to get rich, loopable color from one number t is Inigo
//    Quilez's cosine palette: three cosines (one per channel) with chosen
//    offset/amplitude/frequency/phase. Feed it position, time, or the cell id
//    and you get smooth gradients that cycle without ever hitting a seam.
//
// This shader: a row of boxes, one per cell across the panel, each rotating on
// its own clock, each tinted from the palette by its cell id + time.
//
// Run: ...render tutorial/04-transforms-repeat-color.glsl --dry-run 120 --no-listen
// =============================================================================
// iChannel0: milk

mat2 rot(float a) { float s = sin(a), c = cos(a); return mat2(c, -s, s, c); }

float sdBox(vec2 p, vec2 b)
{
    vec2 d = abs(p) - b;
    return length(max(d, 0.0)) + min(max(d.x, d.y), 0.0);
}

// IQ cosine palette: col(t) = a + b * cos(2pi*(c*t + d)). Tweak the four vecs
// to taste — this set sweeps through teal/orange/magenta.
vec3 palette(float t)
{
    vec3 a = vec3(0.5, 0.5, 0.5);
    vec3 b = vec3(0.5, 0.5, 0.5);
    vec3 c = vec3(1.0, 1.0, 1.0);
    vec3 d = vec3(0.0, 0.33, 0.67);
    return a + b * cos(6.28318 * (c * t + d));
}

void mainImage(out vec4 fragColor, in vec2 fragCoord)
{

    vec4 mid   = texelFetch(iChannel0, ivec2(1, 0), 0);
    vec4 treb  = texelFetch(iChannel0, ivec2(2, 0), 0);
    vec4 vol   = texelFetch(iChannel0, ivec2(3, 0), 0);
    vec4 sub   = texelFetch(iChannel0, ivec2(4, 0), 0);
    vec4 theta = texelFetch(iChannel0, ivec2(5, 0), 0);
    vec4 meta  = texelFetch(iChannel0, ivec2(6, 0), 0);


    vec2 p = (fragCoord - 0.5 * iResolution.xy) / iResolution.y;
    vec2 uv = p;
    float cell = 8.0;                       // one box per 1.0 units of width
    float id = round(((p.x) / cell));           // which copy this pixel falls in
    p.x -= cell * id;                       // fold into that cell, centered on 0

    // Each tile spins on its own phase (offset by id) so the row shimmers
    // instead of marching in lockstep.
    p = rot(id * 1.0) * p + id;

    // p = (sin(uv.y + sub.w) * id) * (cos(uv.x + sub.w) * id);
    float d = sdBox(uv + uv.x * (sub.w + 1.2), uv + uv.y * (vol.w) + 1.0);
    d = abs(d);

    // Color by cell id (a stable per-tile hue) drifting slowly over time.
    vec3 tint = palette(sin(iTime + id));

    // Soft-fill the box; a touch of inner glow via the negative inside-distance.
    float fill = 1.0 - smoothstep(0.0, 0.15, d);
    vec3 col = tint * fill;

    fragColor = vec4(col, 1.0);
}
