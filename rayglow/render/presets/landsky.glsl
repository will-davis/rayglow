// Pacific Evening
// Fork of Pacific Morning https://www.shadertoy.com/view/7XlSRr
// By Noztol

float hash(float p) {
    p = fract(p * .1031);
    p *= p + 33.33;
    p *= p + p;
    return fract(p);
}

float noise(float x) {
    float i = floor(x);
    float f = fract(x);
    f = f * f * (3.0 - 2.0 * f);
    return mix(hash(i), hash(i + 1.0), f);
}

// Creates a swirling, flowing dynamic texture within a band.
float getFluidSkyFlow(vec2 uv, float speed, float scale) {
    float tc = iTime * speed;
    vec2 p = uv * scale;
    p.x -= tc;
    p.y += sin(p.x * 2.0 + tc) * 0.2;
    p.x -= abs(sin(p.y * 3.0 + tc * 0.5)) * 0.1;

    float f = sin(p.x * 2.0) * cos(p.y * 3.0);
    p *= 2.0;
    f += 0.5 * sin(p.x * 3.0) * cos(p.y * 2.0);
    p *= 2.0;
    f += 0.25 * sin(p.x * 4.0) * cos(p.y * 1.0);

    return f;
}

// Triangle SDF for pine tree layers
float sdTri(vec2 p, float base, float tip, float width) {
    float dY = max(base - p.y, p.y - tip);
    float frac = clamp((p.y - base) / (tip - base), 0.0, 1.0);
    float curWidth = width * (1.0 - frac);
    float dX = abs(p.x) - curWidth;
    return max(dX, dY);
}

// Generates a layered conifer/pine tree
float sdTree(vec2 p, vec2 pos, float scale) {
    vec2 q = (p - pos) / scale;

    // Slight wind sway
    q.x -= sin(iTime * 1.5 + pos.x * 10.0) * 0.02 * max(0.0, q.y);

    // Trunk
    float d = max(abs(q.x) - 0.03, max(-q.y, q.y - 0.2));

    // Jagged canopy layers
    d = min(d, sdTri(q, 0.1, 0.45, 0.25));
    d = min(d, sdTri(q, 0.25, 0.65, 0.2));
    d = min(d, sdTri(q, 0.45, 0.85, 0.15));
    d = min(d, sdTri(q, 0.65, 1.0, 0.1));
    return d * scale;
}

float getSilhouettes(vec2 uv, float aspect) {
    float d = 1.0;

    // Left Landmass
    float w1 = 0.05 * exp(-12.0 * pow(uv.x - aspect*0.1, 2.0));
    float c1 = 0.26 - 0.01 * uv.x;
    d = min(d, max(uv.y - (c1 + w1), (c1 - w1) - uv.y));

    // Center Island
    float w2 = 0.03 * exp(-30.0 * pow(uv.x - aspect*0.52, 2.0));
    float c2 = 0.28;
    d = min(d, max(uv.y - (c2 + w2), (c2 - w2) - uv.y));

    // Left Pine Trees
    d = min(d, sdTree(uv, vec2(aspect*0.05, 0.31), 0.18));
    d = min(d, sdTree(uv, vec2(aspect*0.11, 0.30), 0.22));
    d = min(d, sdTree(uv, vec2(aspect*0.17, 0.28), 0.14));
    d = min(d, sdTree(uv, vec2(aspect*0.23, 0.26), 0.09));

    // Center Pine Trees
    d = min(d, sdTree(uv, vec2(aspect*0.48, 0.30), 0.07));
    d = min(d, sdTree(uv, vec2(aspect*0.51, 0.31), 0.10));
    d = min(d, sdTree(uv, vec2(aspect*0.54, 0.30), 0.06));

    return d;
}

void mainImage( out vec4 fragColor, in vec2 fragCoord ) {
    vec2 uv = fragCoord/iResolution.xy;
    float aspect = iResolution.x/iResolution.y;
    uv.x *= aspect;
    float nx = fragCoord.x / iResolution.x;
    float aa = 1.5 / iResolution.y;


    vec3 cSky1 = vec3(0.1, 0.4, 0.75);   // Deep Blue top
    vec3 cSky2 = vec3(0.3, 0.7, 0.9);    // Cyan
    vec3 cSky3 = vec3(0.65, 0.4, 0.7);   // Purple band
    vec3 cSky4 = vec3(0.95, 0.5, 0.1);   // Orange sunset band
    vec3 cSky5 = vec3(1.0, 0.85, 0.1);   // Yellow horizon glow

    vec3 cMtn1 = vec3(0.45, 0.5, 0.6);   // Back mountains
    vec3 cMtn2 = vec3(0.35, 0.4, 0.5);   // Mid mountains
    vec3 cMtn3 = vec3(0.2, 0.25, 0.35);  // Front mountains

    vec3 cBay1 = vec3(1.0, 0.85, 0.15);  // Bright yellow water (near horizon)
    vec3 cBay2 = vec3(1.0, 0.65, 0.1);   // Golden water
    vec3 cBay3 = vec3(0.95, 0.45, 0.05); // Orange water
    vec3 cBay4 = vec3(0.85, 0.25, 0.05); // Dark orange front water

    vec3 cRock = vec3(0.08, 0.1, 0.12);  // Near-black silhouette with blue tint

    float s1 = 0.65 + 0.25 * smoothstep(0.1, 0.9, nx);
    float s2 = 0.5 - 0.1 * nx + 0.05*sin(nx*4.0);
    float s3 = 0.4 + 0.02*sin(nx*6.0);
    float s4 = 0.35 + 0.01*sin(nx*10.0);

    float horizon = 0.3;
    float mtn1_line = horizon + 0.12 + 0.02*sin(nx*5.0) + 0.03*cos(nx*3.0);
    float mtn2_line = horizon + 0.07 + 0.015*sin(nx*9.0) + 0.01*sin(nx*15.0);
    float mtn3_line = horizon + 0.03 + 0.015*sin(nx*12.0) + 0.01*sin(nx*27.0);

    float bay1_line = 0.26 + 0.02 * sin(nx * 4.0) - 0.01 * nx;
    float bay2_line = 0.22 + 0.03 * sin(nx * 4.5 - 0.5) - 0.02 * nx;
    float bay3_line = 0.17 + 0.04 * sin(nx * 5.0 - 1.0) - 0.03 * nx;

    float fg1_line = 0.12 + 0.03 * sin(nx * 6.0) - 0.02 * cos(nx * 9.0) + 0.01 * sin(nx * 20.0);
    float fg2_line = 0.05 + 0.02 * cos(nx * 5.0) + 0.015 * sin(nx * 14.0);

    vec3 col = vec3(0.0);

    // 1. Sky Flow Base
    float f1 = getFluidSkyFlow(uv, 0.4, 4.0);
    vec3 skyFlow1 = mix(cSky1, vec3(0.4, 0.6, 0.8), smoothstep(-0.2, 0.2, f1));
    float f2 = getFluidSkyFlow(uv, 0.3, 5.0);
    vec3 skyFlow2 = mix(cSky2, vec3(0.5, 0.8, 0.95), smoothstep(-0.2, 0.2, f2));

    // Blend Sky Bands using smoothstep for crisp vector edges
    col = skyFlow1;
    col = mix(col, skyFlow2, smoothstep(s1 + aa, s1 - aa, uv.y));
    col = mix(col, cSky3, smoothstep(s2 + aa, s2 - aa, uv.y));
    col = mix(col, cSky4, smoothstep(s3 + aa, s3 - aa, uv.y));
    col = mix(col, cSky5, smoothstep(s4 + aa, s4 - aa, uv.y));

    // 2. Mountains
    col = mix(col, cMtn1, smoothstep(mtn1_line + aa, mtn1_line - aa, uv.y));
    col = mix(col, cMtn2, smoothstep(mtn2_line + aa, mtn2_line - aa, uv.y));
    col = mix(col, cMtn3, smoothstep(mtn3_line + aa, mtn3_line - aa, uv.y));

    // 3. Water and Foreground
    vec3 lowerHalf = cBay1;
    lowerHalf = mix(lowerHalf, cBay2, smoothstep(bay1_line + aa, bay1_line - aa, uv.y));
    lowerHalf = mix(lowerHalf, cBay3, smoothstep(bay2_line + aa, bay2_line - aa, uv.y));
    lowerHalf = mix(lowerHalf, cBay4, smoothstep(bay3_line + aa, bay3_line - aa, uv.y));

    lowerHalf = mix(lowerHalf, cRock, smoothstep(fg1_line + aa, fg1_line - aa, uv.y));
    lowerHalf = mix(lowerHalf, cRock * 0.6, smoothstep(fg2_line + aa, fg2_line - aa, uv.y));

    col = mix(col, lowerHalf, smoothstep(horizon + aa, horizon - aa, uv.y));

    // 4. Island Reflections in Water
    if (uv.y < horizon) {
        vec2 reflUv = uv;
        reflUv.y = horizon + (horizon - uv.y);
        float silRefl = getSilhouettes(reflUv, aspect);
        float reflMask = smoothstep(0.005, 0.0, silRefl);

        reflMask *= smoothstep(0.1, horizon, uv.y) * 0.4;
        col = mix(col, cRock * 0.8, reflMask);
    }

    // 5. Solid Foreground Silhouettes
    float sil = getSilhouettes(uv, aspect);
    float silMask = smoothstep(0.003, 0.0, sil);
    col = mix(col, cRock, silMask);

    fragColor = vec4(col, 1.0);
}
