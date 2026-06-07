float hash(vec2 p) {
    p = fract(p * vec2(123.34, 456.21));
    p += dot(p, p + 45.32);
    return fract(p.x * p.y);
}

float noise(vec2 p) {
    vec2 i = floor(p);
    vec2 f = fract(p);

    float a = hash(i);
    float b = hash(i + vec2(1.0, 0.0));
    float c = hash(i + vec2(0.0, 1.0));
    float d = hash(i + vec2(1.0, 1.0));

    vec2 u = f * f * (3.0 - 2.0 * f);

    return mix(a, b, u.x) +
           (c - a) * u.y * (1.0 - u.x) +
           (d - b) * u.x * u.y;
}

float fbm(vec2 p) {
    float v = 0.0;
    float a = 0.5;

    for (int i = 0; i < 5; i++) {
        v += a * noise(p);
        p *= 2.0;
        a *= 0.5;
    }
    return v;
}

vec3 palette(float t) {
    vec3 a = vec3(0.10, 0.05, 0.15);
    vec3 b = vec3(0.40, 0.35, 0.55);
    vec3 c = vec3(1.0, 1.0, 1.0);
    vec3 d = vec3(0.0, 0.10, 0.20);

    return a + b * cos(6.28318 * (c * t + d));
}

void mainImage(out vec4 fragColor, in vec2 fragCoord)
{
    vec2 uv = fragCoord.xy / iResolution.xy;
    vec2 p = uv - 0.5;
    p.x *= iResolution.x / iResolution.y;

    float t = iTime * 0.05;

    // flowing domain warp
    vec2 flow = vec2(
        fbm(p * 2.0 + vec2(0.0, t)),
        fbm(p * 2.0 + vec2(0.0, -t))
    );

    vec2 q = p + 0.35 * (flow - 0.5);

    // layered glowing blobs
    float n1 = fbm(q * 3.0 + t);
    float n2 = fbm(q * 6.0 - t * 1.3);

    float blobs = smoothstep(0.3, 0.9, n1 + 0.6 * n2);

    // radial glow structure
    float r = length(q);
    float glow = exp(-3.0 * r) * (0.6 + 0.4 * sin(t * 2.0 + n1 * 6.0));

    float intensity = blobs * 0.7 + glow;

    // color gradient field
    float colorField = fbm(q * 2.0 + intensity + t);
    vec3 col = palette(colorField + intensity * 0.5);

    // boost glow
    col += vec3(0.3, 0.5, 0.8) * glow;
    col *= intensity * 2.0;

    // tone mapping
    col = 1.0 - exp(-col);

    fragColor = vec4(col, 1.0);
}
