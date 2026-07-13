#define S smoothstep

// iChannel0: milk
//
// v3 PORT EXEMPLAR (2026-07).  The milk texture went 13x1 -> 16x3; this
// file shows the mechanical mapping, read by read:
//
//   old read                          new read
//   sub   = texelFetch(.., (4,0))  -> b0  = texelFetch(.., ivec2(0,0))
//           (true low end; .w env)          (.y env0 is the old .w's feel;
//                                            .z env1 / .w env2 add momentum)
//   vol   = texelFetch(.., (3,0))  -> vol = texelFetch(.., ivec2(8,0))
//   theta = texelFetch(.., (5,0))  -> row 1: texelFetch(.., ivec2(b,1))
//   meta.x  (sub's theta)          -> b0m.x (theta0) — or .y/.z for the
//                                     flywheel tiers
//   meta.y/.z (age/live)           -> texelFetch(.., ivec2(7,2)).x/.y
//   legacy scalars 1:1 (if you just want the old values): (9,2) bass mid
//   treb vol · (10,2) bass_att mid_att treb_att sub · (11,2).x sub_att
//
// This port also UPGRADES the drivers to show the v3 point: the wave phase
// now rides theta1 (kick-spun, keeps turning after the hit — the flywheel)
// and the amplitude rides env1 (punchy attack, slow decay).  For the exact
// pre-v3 behavior use b0m.x and b0.y instead.

vec4 Line(vec2 uv, float theta, float volamp, float speed, float height, vec3 col) {
    uv.y += S(1., 0., abs(uv.x)) * sin(theta * speed + uv.x * height) * volamp * 0.12;
    return vec4(S(.11 * S(.5, .9, abs(uv.x)), 0., abs(uv.y) - .007) * col, 1.0) * S(1., .3, abs(uv.x));
}

void mainImage(out vec4 O, in vec2 I) {
    vec2 uv  = (I - 0.5 * iResolution.xy) / (iResolution.y * 5.0);
    vec4 b0  = texelFetch(iChannel0, ivec2(0, 0), 0);  // 20-60Hz: imm env0 env1 env2
    vec4 b0m = texelFetch(iChannel0, ivec2(0, 1), 0);  // theta0/1/2, onset
    O = vec4(0.);
    for (float i = 0.; i <= 10.; i += 1.0) {
        float t = i / 10.;
        O += Line(uv, b0m.y * 4., S(0., 4., b0.z) * 1.6 + .5, t + .5, 4. + t,
                  vec3(sin(t * 2.5), sin(t * 5.), sin(t * 10.)));
    }
}
