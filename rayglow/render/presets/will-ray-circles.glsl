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
float map(vec3 p, float orbrad)
{
    float s1 = length(p - vec3(cos(iTime) * 2. * orbrad, 0.0, sin(iTime - pi) * 2. * orbrad)) - orbrad;
    float s2 = length(p - vec3(cos(iTime - pi)* 2. * orbrad, 0.0, sin(iTime) * 2. * orbrad)) - orbrad;
    return min(s1, s2);                 // union — nearest surface of the two
}

void mainImage(out vec4 fragColor, in vec2 fragCoord)
{
    vec4 bass  = texelFetch(iChannel0, ivec2(0, 0), 0);
    vec4 mid   = texelFetch(iChannel0, ivec2(1, 0), 0);
    vec4 treb  = texelFetch(iChannel0, ivec2(2, 0), 0);
    vec4 vol   = texelFetch(iChannel0, ivec2(3, 0), 0);
    vec4 sub   = texelFetch(iChannel0, ivec2(4, 0), 0);
    vec4 theta = texelFetch(iChannel0, ivec2(5, 0), 0);
    vec4 meta  = texelFetch(iChannel0, ivec2(6, 0), 0);
    vec2 uv = (fragCoord - 0.5 * iResolution.xy) / iResolution.y;
    float fc = length(uv);
    vec3 ro = vec3(0.0, 0.0, 4.0);          // camera, 4 units back on +z
    vec3 rd = normalize(vec3(uv, -1.5));    // through the pixel, into the screen

    // March.
    float t = 0.0;
    bool hit = false;
    int steps = 0;
    float orbrad = (sub.w / 1.5) + 0.3;
    for (int i = 0; i < 60; i++)
    {
        vec3 p = ro + rd * t;
        float d = map(p, orbrad);
        if (d < 0.001) { hit = true; steps = i; break; }   // arrived at a surface
        t += d;                                 // safe leap
        if (t > 20.0) break;                    // escaped — background
    }

    // Background: a faint vertical gradient so misses aren't pure black.
    vec3 col = vec3(fc, fc, fc) * (uv.y + 0.3);

    if (hit)
    {
        // Depth shading placeholder: map distance-from-camera into brightness.
        // The camera sits at t~4; surfaces span roughly t in [2.5, 5].
        // float depth = clamp(1.0 - (t - 2.5) / 3.0, 0.0, 1.0);
        float depth = clamp(1.0 - (pow(t, 1.10) - 3.0) / 3.0, 0.0, 1.0);
        if (steps > edgethreshold) col = vec3(0.0, 0.0, 0.0);
        if (steps < edgethreshold) col = vec3(0.0, 0.95, 0.5) * depth;
    }
    fragColor = vec4(col, 1.0);
}
