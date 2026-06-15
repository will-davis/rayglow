// 03 — SIGNED DISTANCE FIELDS, AND COMBINING THEM
// =============================================================================
// So far "distance" has been distance to a point or a line, always >= 0. The
// big upgrade is the SIGN. A Signed Distance Function (SDF) returns:
//
//        d < 0   you are INSIDE the shape   (|d| = depth below the surface)
//        d = 0   you are exactly ON the boundary
//        d > 0   you are OUTSIDE             (|d| = distance to the surface)
//
// That's it — but the sign is what makes shapes COMPOSABLE with plain min/max,
// and (next file) what raymarching needs to know which way to step. Two
// staples:
//
//        sdCircle(p, r) = length(p) - r          // negative inside, by r
//        sdBox(p, b):    fold to one quadrant with abs(p), then measure to the
//                        corner; the max()/min() combo handles inside vs out.
//
// Read sdBox once and then just trust it; the derivation is a known result.
//
// SEEING THE FIELD -----------------------------------------------------------
// The single most useful exercise for SDF intuition is to stop drawing the
// SHAPE and instead draw the FIELD: color by the value of d everywhere. You
// get inside/outside as two tints, evenly-spaced rings as iso-distance
// contours (like a topographic map — every ring is "one more step of distance
// from the surface"), and a bright wall at d=0. Once you can read that map,
// boolean operations stop being magic. This shader renders exactly that map.
//
// BOOLEAN OPS ARE JUST min / max --------------------------------------------
// Because the value at a point is "distance to the nearest surface", combining
// fields is arithmetic on those distances:
//
//        union(A,B)        = min(dA, dB)     // nearest of either surface
//        intersection(A,B) = max(dA, dB)
//        subtract  B from A= max(dA, -dB)    // flip B inside-out, intersect
//
// SMOOTH UNION (smin) --------------------------------------------------------
// min() gives a hard crease where two shapes meet. smin() blends them with a
// liquid fillet — the trick that makes SDF scenes look organic and "melty".
// k sets the blend radius. This is the workhorse you'll use forever.
//
// Run: ...render tutorial/03-sdf-2d-and-ops.glsl --dry-run 90 --no-listen
// =============================================================================
// iChannel0: milk

vec3 palette( float t) {
    vec3 a = vec3(0.3, 0.3, 0.5);
    vec3 b = vec3(-0.3, 0.3, 0.3);
    vec3 c = vec3(1.0, 1.0, 1.0);
    vec3 d = vec3(1.8, -1.0, 0.9);
    return a + b*cos( 6.28318*(c*t+d) );
}

float sdCircle(vec2 p, float r) { return length(p) - r; }

float sdBox(vec2 p, vec2 b)
{
    vec2 d = abs(p) - b;                 // fold into the first quadrant
    return length(max(d, 0.0))           // outside distance (corner region)
         + min(max(d.x, d.y), 0.0);      // inside distance (negative)
}

// Polynomial smooth minimum (Inigo Quilez). k = blend radius; k->0 is plain min.
float smin(float a, float b, float k)
{
    float h = clamp(0.5 + 0.5 * (b - a) / k, 0.0, 1.0);
    return mix(b, a, h) - k * h * (1.0 - h);
}

// The scene as ONE signed distance field. A box and a circle, smooth-unioned.
// The circle slides left/right so you can watch the fillet form and break.
float scene(vec2 p, float subspeed, float volume, float subsize, float treble)
{
    float bigball = sdCircle(p - vec2(0.0, 0.0), 0.2 * volume + 0.4);
    float ballone = sdCircle(p - vec2(1.2 + subsize / 4., 0.0), 0.2 * subsize + 0.1);
    float balltwo = sdCircle(p + vec2(1.2 + subsize / 4., 0.0), 0.2 * subsize + 0.1);
    float ballthree = sdCircle(p - vec2(2.4 + subsize / 2., 0.0), 0.2 * treble + 0.1);
    float ballfour = sdCircle(p + vec2(2.4 + subsize / 2., 0.0), 0.2 * treble + 0.1);
    float sminone = smin(ballone, balltwo, 0.25);
    float smintwo = smin(sminone, ballthree, 0.25);
    float sminthree = smin(smintwo, ballfour, 0.25);
    float sminfour = smin(sminthree, bigball, 0.25);
    return sminfour;
}

void mainImage(out vec4 fragColor, in vec2 fragCoord)
{
    vec4 sub   = texelFetch(iChannel0, ivec2(4, 0), 0);
    vec4 vol   = texelFetch(iChannel0, ivec2(3, 0), 0);
    vec4 theta = texelFetch(iChannel0, ivec2(5, 0), 0);
    vec4 treb  = texelFetch(iChannel0, ivec2(2, 0), 0);
    vec4 meta  = texelFetch(iChannel0, ivec2(6, 0), 0);
    vec4 mid   = texelFetch(iChannel0, ivec2(1, 0), 0);
    vec2 p = (fragCoord - 0.5 * iResolution.xy) / iResolution.y; 
    float subspeed = meta.x;
    float subsize = sub.w;
    float volume = mid.w;
    float treble = treb.w;
    float d = scene(p, subspeed, volume, subsize, treble);
    // d = ((0.065 * (sin(theta.x) + 1.) + 0.01)) / (d - 0.2);
    // --- COMPOSITE THE SHAPES ONTO A BACKGROUND -----------------------------
    // Before, every pixel got a palette tint from `d` (the whole field was
    // colored). Now we treat `d` purely as a MASK SOURCE: the SDF says WHERE the
    // shapes are, and mix() lays each layer over a backdrop. Each mix() reads as
    // "paint this color where the mask is 1; keep what's underneath where it's 0."

    // 0. the background, painted everywhere first: a dark vertical gradient so
    //    it's clearly a backdrop and not part of the shapes. Swap in anything.
    vec3 col = palette(length(p * .2) - theta.y * 0.5);
    // col = .005 / col;

    // 1. the FILL mask: 1 inside the shape (d < 0), 0 outside, soft at the edge.
    //    This is just the SDF's sign turned into an alpha channel via smoothstep.
    float fill = 1.0 - smoothstep(-0.01, 0.01, d);

    // 2. the RING mask: a band hugging the boundary where |d| is small. The far
    //    edge of THIS smoothstep (0.03) sets how thick the white ring is — raise
    //    it for a fatter rim, lower it for a hairline.
    float ring = 1.0 - smoothstep(0.0, 0.05, abs(d));

    // 3. the disc color (still audio-driven from the palette).
    vec3 ballColor = palette(d - meta.x / 1.0) - d;

    // 4. lay it down back-to-front. Order matters: the ring is painted LAST so
    //    it sits on top of the fill and stays visible as a clean white outline.
    col = mix(col, ballColor, fill);     // discs over the background
    col = mix(col, vec3(0.0), ring);     // white rims over the discs

    fragColor = vec4(col, 1.0);
}
