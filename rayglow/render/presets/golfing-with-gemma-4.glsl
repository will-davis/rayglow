void mainImage(out vec4 O, vec2 C) {
    vec2 u = (C * 2. - iResolution.xy) / iResolution.y;
    float t = iTime * .5, s = 0.;
    for(float i = 1.; i < 6.; i++) {
        u += sin(u.yx * i + t) / i;
        s += abs(sin(u.x + u.y + t)) / i;
    }
    O = vec4(0.5 + 0.5 * cos(t + s * 3. + vec3(0, 2, 4)), 1);
}
