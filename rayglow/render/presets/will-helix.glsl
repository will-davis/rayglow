#define S smoothstep

// iChannel0: milk

vec4 Line(vec2 uv, float theta, float volamp, float speed, float height, vec3 col) {
    uv.y += S(1., 0., abs(uv.x)) * sin(theta * speed + uv.x * height) * volamp * 0.12;
    return vec4(S(.11 * S(.5, .9, abs(uv.x)), 0., abs(uv.y) - .007) * col, 1.0) * S(1., .3, abs(uv.x));
}

void mainImage(out vec4 O, in vec2 I) {
    vec2 uv    = (I - 0.5 * iResolution.xy) / (iResolution.y * 5.0);
    vec4 sub   = texelFetch(iChannel0, ivec2(4, 0), 0);
    vec4 vol   = texelFetch(iChannel0, ivec2(3, 0), 0);
    vec4 theta = texelFetch(iChannel0, ivec2(5, 0), 0);
    vec4 meta  = texelFetch(iChannel0, ivec2(6, 0), 0);
    float volamp = S(0.,1.,meta.x);
    // float volamp = S(0.,3.,sub.w);
    O = vec4 (0.);
    for (float i = 0.; i <= 10.; i += 1.0) {
        float t = i / 10.;
        O += Line(uv, meta.x * 4., S(0.,4.,sub.w) * 1.6 + .5, t + .5, 4. + t, vec3(sin(t * 2.5), sin(t * 5.), sin(t * 10.)));
    }
}
