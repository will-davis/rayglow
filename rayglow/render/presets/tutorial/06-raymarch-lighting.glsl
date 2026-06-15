// 06 — MAKING 3D LOOK 3D: NORMALS, LIGHT, AND A CAMERA
// =============================================================================
// File 05 found WHERE the ray hits. The flat result proved that position alone
// isn't enough — a surface looks solid only when its brightness depends on how
// it's TILTED relative to a light. That tilt is the surface NORMAL, and the SDF
// hands it to us almost for free.
//
// NORMALS FROM THE SDF GRADIENT ----------------------------------------------
// An SDF increases fastest in the direction pointing straight out of the
// surface — so its gradient (the vector of partial derivatives) IS the outward
// normal. We don't have a formula for the derivative of a complicated map(), so
// we estimate it numerically: sample map() a hair to each side along x, y, z
// and take the differences (central differences). Normalize and you have the
// unit normal. This little function works for ANY scene you put in map().
//
//        n.x ~ map(p + dx) - map(p - dx)        (and likewise y, z)
//
// DIFFUSE (LAMBERT) LIGHTING -------------------------------------------------
// The most basic believable shading: a surface is brightest when it faces the
// light head-on, dimming to dark as it turns away. "How much it faces the
// light" is dot(normal, directionToLight): 1 when aligned, 0 at a right angle,
// negative when facing away (clamp those to 0). Add a little flat AMBIENT term
// so shadowed sides aren't pure black, and that already looks like a lit solid.
//
//        bright = ambient + max(dot(n, lightDir), 0.0)
//
// A REAL CAMERA --------------------------------------------------------------
// File 05's camera was bolted facing -z. A reusable camera is just an
// orthonormal basis aimed from the eye `ro` at a target `ta`:
//
//        forward = normalize(ta - ro)
//        right   = normalize(cross(forward, worldUp))
//        up      = cross(right, forward)
//        rayDir  = normalize(uv.x*right + uv.y*up + focal*forward)
//
// Swing `ro` around the origin with iTime and the whole scene orbits, with
// correct perspective, for free.
//
// THE SCENE ------------------------------------------------------------------
// A ground plane (SDF of a plane is just signed height) smooth-unioned (smin,
// from file 03 — it works in 3D unchanged) with a bobbing sphere, so you can
// see the soft fillet where the ball meets the floor, properly lit. This is a
// complete, tiny raymarcher; grow it by editing map() and the shading.
//
// Run: ...render tutorial/06-raymarch-lighting.glsl --dry-run 150 --no-listen
// =============================================================================

float smin(float a, float b, float k)
{
    float h = clamp(0.5 + 0.5 * (b - a) / k, 0.0, 1.0);
    return mix(b, a, h) - k * h * (1.0 - h);
}

float map(vec3 p)
{
    float ground = p.y + 1.0;                                    // plane y = -1
    float ball   = length(p - vec3(0.0, 0.25 * sin(iTime * 1.5) - 0.2, 0.0)) - 0.7;
    return smin(ground, ball, 0.4);                              // soft weld
}

// Surface normal = normalized gradient of the SDF (central differences).
vec3 calcNormal(vec3 p)
{
    vec2 e = vec2(0.001, 0.0);
    return normalize(vec3(
        map(p + e.xyy) - map(p - e.xyy),
        map(p + e.yxy) - map(p - e.yxy),
        map(p + e.yyx) - map(p - e.yyx)));
}

void mainImage(out vec4 fragColor, in vec2 fragCoord)
{
    vec2 uv = (fragCoord - 0.5 * iResolution.xy) / iResolution.y;

    // Orbiting camera aimed at the origin.
    float a = iTime * 0.5;
    vec3 ro = vec3(3.0 * sin(a), 0.6, 3.0 * cos(a));    // eye circles the scene
    vec3 ta = vec3(0.0, -0.2, 0.0);                     // look-at target
    vec3 fwd = normalize(ta - ro);
    vec3 rgt = normalize(cross(fwd, vec3(0.0, 1.0, 0.0)));
    vec3 up  = cross(rgt, fwd);
    vec3 rd  = normalize(uv.x * rgt + uv.y * up + 1.6 * fwd);   // 1.6 = focal len

    // March (same loop as file 05).
    float t = 0.0;
    bool hit = false;
    for (int i = 0; i < 100; i++)
    {
        vec3 p = ro + rd * t;
        float d = map(p);
        if (d < 0.001) { hit = true; break; }
        t += d;
        if (t > 30.0) break;
    }

    // Sky for misses: a soft gradient.
    vec3 col = mix(vec3(0.05, 0.07, 0.12), vec3(0.15, 0.2, 0.35), uv.y + 0.5);

    if (hit)
    {
        vec3 p = ro + rd * t;
        vec3 n = calcNormal(p);

        vec3 lightDir = normalize(vec3(0.7, 0.8, 0.2));
        float diff = max(dot(n, lightDir), 0.0);          // Lambert
        float amb  = 0.18;                                 // fill so shadows live

        // Tint the ground and ball differently using the hit height, just to
        // show that the normal/lighting math is independent of base color.
        vec3 base = mix(vec3(0.8, 0.5, 0.3), vec3(0.4, 0.7, 1.0),
                        smoothstep(-0.9, 0.4, p.y));
        col = base * (amb + diff);
    }

    // A touch of gamma so the midtones don't read muddy on the LEDs.
    col = pow(clamp(col, 0.0, 1.0), vec3(0.8));
    fragColor = vec4(col, 1.0);
}
