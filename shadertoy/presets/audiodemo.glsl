#define S smoothstep


vec4 Line(vec2 uv, float speed, float height, vec3 col) {
    uv.y += S(1., 0., abs(uv.x)) * sin(iTime * speed + uv.x * height) * .3;
    return vec4(S(.06 * S(.2, .9, abs(uv.x)), 0., abs(uv.y) - .004) * col, 1.0) * S(1., .3, abs(uv.x));
}

void mainImage(out vec4 O, in vec2 I) {
    vec2 uv = (I - 0.5 * iResolution.xy) / (iResolution.y * 5.0);
    O = vec4 (0.);
    for (float i = 0.; i <= 10.; i += 1.0) {
        float treble = texture(iChannel0, vec2(0.99, 0.25)).r;
        float bass = texture(iChannel0, vec2(0.01, 0.25)).r;
        float t = i / 10.;
        O += Line(uv, (1. / 10.) + t * 2., treble*8. + t, vec3(.2 + t * .7, .2 + t * .4, sin(t * 10. + bass)));
    }
}
