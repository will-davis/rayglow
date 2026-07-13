// 05 — INTO 3D: RAYMARCHING
// =============================================================================
// Everything so far answered "what color is this 2D pixel?" directly. 3D needs
// one more step, but the foundation is identical: we still have an SDF, just in
// 3D now — a function map(vec3 p) returning signed distance to the nearest
// surface (negative inside). length(p) - 1.0 is a unit sphere at the origin,
// exactly the 2D circle with one more component.
//
// THE PROBLEM 3D ADDS --------------------------------------------------------
// A pixel is a point on a flat screen, but the scene has depth. So each pixel
// casts a RAY from the camera, through that pixel, into the scene, and we ask:
// where (if anywhere) does this ray first touch a surface? Answer that and we
// know what the pixel sees.
//
// SPHERE TRACING (the clever part) ------------------------------------------
// The SDF makes "march along the ray until you hit something" cheap and exact.
// At any point p, map(p) is the distance to the NEAREST surface in ANY
// direction — so a step of exactly map(p) along the ray is the largest step
// guaranteed not to overshoot any surface. Stand at the camera, look up the
// distance, jump that far, repeat:
//
//     t = 0
//     repeat:
//         p = ro + rd * t          // current point along the ray
//         d = map(p)               // safe distance we may advance
//         if d < EPS: HIT          // basically touching a surface
//         t += d                   // leap forward
//         if t > FAR: MISS         // ray escaped to infinity
//
// Near a surface the steps shrink to a crawl (d -> 0), which is why it
// converges precisely onto the boundary. In open space it takes giant strides,
// which is why it's fast. That's the entire algorithm — a loop and an SDF.
//
// BUILDING THE RAY -----------------------------------------------------------
// Put the camera at ro, a few units back on +z, looking toward -z (into the
// screen). The ray direction for a pixel uses our aspect-correct uv for the
// x/y aim and a fixed -z for "forward"; the z magnitude is the focal length
// (bigger = narrower field of view). normalize() so each step of `t` is one
// world unit. (File 06 replaces this fixed camera with a real orbiting one.)
//
// WHY THIS LOOKS FLAT --------------------------------------------------------
// We shade a hit by DEPTH only (nearer = brighter), so a single sphere reads
// as a flat disc — depth alone can't show curvature. To make the depth cue
// legible there are TWO spheres at different distances, unioned with min()
// (3D booleans are the same min/max from file 03). They orbit, swapping which
// is nearer, so you can read the field as genuinely 3D. Real surface shading —
// normals and light — is file 06, and it's a small addition.
//
// Run: ...render tutorial/05-raymarch-intro.glsl --dry-run 120 --no-listen
// =============================================================================
// The scene SDF in 3D: two spheres, unioned. They swap depth over time.
// 3.14159
// iChannel0: milk

float pi = 3.14159;
int edgethreshold = 12;
vec3 palette( float t) {
    vec3 a = vec3(0.3, 0.3, 0.5);
    vec3 b = vec3(-0.3, 0.3, 0.3);
    vec3 c = vec3(1.0, 1.0, 1.0);
    vec3 d = vec3(1.8, -1.0, 0.9);
    return a + b*cos( 6.28318*(c*t+d) );
}

float smin(float a, float b, float k) 
{
    float h = clamp(0.5 + 0.5 * (b - a) / k, 0.0, 1.0);
    return mix(b, a, h) - k * h * (1.0 - h);
}

float map(vec3 p, float orbrad, float sphererad, float speed)
{

    float s1 = length(p - vec3(cos(speed)      * 2. * orbrad, 0.0, sin(speed - pi) * 2. * orbrad)) - sphererad;
    float s2 = length(p - vec3(cos(speed - pi) * 2. * orbrad, 0.0, sin(speed)      * 2. * orbrad)) - sphererad;
    float smoothed = smin(s1, s2, 1.15);
    return smoothed;                 //smoothed union — nearest surface of the two
}

void mainImage(out vec4 fragColor, in vec2 fragCoord)
{
    // IMPORT AUDIO VARIABLES TO EXPERIMENT WITH — legacy scalars rebuilt on
    // the v3 16x3 milk texture (see textures.py).  Old 13x1 texels 0-4 were
    // (.x imm .y att .z ddt .w env): imm/att now live in the legacy block
    // (9..11,2); env = env0 (row 0 .y) of the nearest v3 band; ddt is gone
    // in v3 (row 1 .w onset is its + half) -> 0.
    vec4 lvl   = texelFetch(iChannel0, ivec2(9, 2), 0);   // bass mid treb vol imm
    vec4 attv  = texelFetch(iChannel0, ivec2(10, 2), 0);  // atts + sub imm
    vec4 bass  = vec4(lvl.x, attv.x, 0.0, texelFetch(iChannel0, ivec2(2, 0), 0).y);
    vec4 mid   = vec4(lvl.y, attv.y, 0.0, texelFetch(iChannel0, ivec2(5, 0), 0).y);
    vec4 treb  = vec4(lvl.z, attv.z, 0.0, texelFetch(iChannel0, ivec2(7, 0), 0).y);
    vec4 vol   = vec4(lvl.w, lvl.w, 0.0, texelFetch(iChannel0, ivec2(8, 0), 0).y);
    vec4 sub   = vec4(attv.w, texelFetch(iChannel0, ivec2(11, 2), 0).x, 0.0,
                      texelFetch(iChannel0, ivec2(0, 0), 0).y);
    // old texel 5 (bass/mid/treb/vol theta) -> theta0 of the nearest v3 band
    vec4 theta = vec4(texelFetch(iChannel0, ivec2(2, 1), 0).x,
                      texelFetch(iChannel0, ivec2(5, 1), 0).x,
                      texelFetch(iChannel0, ivec2(7, 1), 0).x,
                      texelFetch(iChannel0, ivec2(8, 1), 0).x);
    // old texel 6 (.x sub theta, .yzw pkt_age/live/source_domain)
    vec4 meta  = vec4(texelFetch(iChannel0, ivec2(0, 1), 0).x,
                      texelFetch(iChannel0, ivec2(7, 2), 0).xyz);
    float speed = meta.x;
    float sphererad = (vol.w * 2.5 + 2.) / 3.0;
    float orbrad = (sub.w / 1.0) + 0.1;

    // THE CANVAS
    vec2 uv = (fragCoord - 0.5 * iResolution.xy) / iResolution.y;
    float fc = length(uv);
    vec3 ro = vec3(0.0, 0.0, 10.0);          // camera, 10 units back on +z
    vec3 rd = normalize(vec3(uv, -1.5));    // through the pixel, into the screen

    // BACKGROUND: PALETTE LOOPING ON X COORDS 
    vec3 col = vec3((palette(uv.x * 0.2 + iTime/ 4.)));

    // INITIALIZE FOR MARCH.
    float t = 0.0;
    bool hit = false;
    int steps = 0;
    float raydistance = 1.0;
    
    // THE MARCH OF RAYS
    for (int i = 0; i < 60; i++)
    {
        vec3 p = ro + rd * t;
        float d = map(p, orbrad, sphererad, speed);
        if (d < 0.001) { hit = true; steps = i; raydistance = t; break; }   // arrived at a surface
        t += d;                                 // safe leap
        if (t > 20.0) break;                    // escaped — background
    }
    if (hit)
    {
        // Depth shading placeholder: map distance-from-camera into brightness.
        // The camera sits at t~10; surfaces span roughly t in [2.5, 10.0].
        // float depth = clamp(1.0 - (t - 5.5) / 5.0, 0.0, 1.0);
        float depth = clamp(1.0 - (pow(t, 1.10) - 10.0) / 2.0, 0.0, 1.0);
        if (steps > edgethreshold) col = vec3(0.0, 0.0, 0.0);
        if (steps < edgethreshold) col = palette(raydistance * 0.2 + 0.8) * depth;
    }
    fragColor = vec4(col, 1.0);
}
