// Cascade Sunrise
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
    // Lateral flow and swirling distortion
    p.x -= tc;
    p.y += sin(p.x * 2.0 + tc) * 0.2;
    p.x -= abs(sin(p.y * 3.0 + tc * 0.5)) * 0.1;

    // Use a multi-scale approach for detail
    float f = sin(p.x * 2.0) * cos(p.y * 3.0);
    p *= 2.0;
    f += 0.5 * sin(p.x * 3.0) * cos(p.y * 2.0);
    p *= 2.0;
    f += 0.25 * sin(p.x * 4.0) * cos(p.y * 1.0);

    return f;
}

float getSilhouettes(vec2 uv) {
    float d = 1.0;

    // Wind oscillation removed.

    // Left Bonsai/Deciduous Tree
    vec2 p3 = uv - vec2(0.25, 0.1);
    // Sway logic: a subtle, slower sway related to fluid flow rather than quick oscillation.
    float fluidSpeed_bonsai = 0.5;
    float globalSway = sin(iTime * fluidSpeed_bonsai) * 0.02;
    p3.x -= globalSway * max(0.0, p3.y * 1.5);

    // Curved trunk
    float tx = sin(p3.y * 8.0) * 0.04;
    float trunk = length(vec2(p3.x - tx, max(0.0, p3.y - 0.35))) - 0.01 + p3.y*0.02;
    if(p3.y < 0.0) trunk = 1.0;
    d = min(d, trunk);

    // Canopy blobs (with fluttering leaves using multi-scale noise)
    float leafFlutter1 = noise((uv.x + iTime * 0.5) * 30.0) * 0.01;
    float leafFlutter2 = noise((uv.y - iTime) * 30.0) * 0.01;
    float leafFlutter3 = noise((uv.x + uv.y + iTime) * 30.0) * 0.01;

    d = min(d, length(p3 - vec2(-0.06, 0.2)) - 0.06 + leafFlutter1);
    d = min(d, length(p3 - vec2(0.08, 0.24)) - 0.05 + leafFlutter2);
    d = min(d, length(p3 - vec2(-0.02, 0.32)) - 0.07 + leafFlutter3);

    // Ground rock under bonsai (static)
    vec2 pRock = uv - vec2(0.25, 0.1);
    float ground = length(vec2(pRock.x, pRock.y*3.0)) - 0.15 + noise(uv.x*10.0)*0.02;
    d = min(d, ground);

    return d;
}

void mainImage( out vec4 fragColor, in vec2 fragCoord ) {
    vec2 uv = fragCoord/iResolution.xy;
    uv.x *= iResolution.x/iResolution.y;

    // Blank canvas animation
    float drawProgress = iTime * 0.4;
    if (uv.x > drawProgress) {
        fragColor = vec4(0.9, 0.85, 0.8, 1.0);
        return;
    }

    // Sky boundaries (preserved curves with fluid flow parallax)
    float t1 = uv.x - iTime * 0.02;
    float t2 = uv.x - iTime * 0.04;
    float t3 = uv.x - iTime * 0.06;
    float t4 = uv.x - iTime * 0.08;

    float s1 = 0.82 + sin(t1 * 1.5)*0.04 + noise(t1*3.0)*0.03;
    float s2 = 0.68 + sin(t2 * 2.0)*0.05 + noise(t2*4.0)*0.03;
    float s3 = 0.55 + sin(t3 * 1.2)*0.04 + noise(t3*2.0)*0.02;
    float s4 = 0.45 + sin(t4 * 2.5)*0.03 + noise(t4*5.0)*0.02;

    // Mountain Segments (Static anchors from provided code)
    float m1 = 0.38 + noise(uv.x * 3.0)*0.08 + noise(uv.x * 8.0)*0.03;
    float m2 = 0.32 + noise(uv.x * 2.5 + 5.0)*0.1 + noise(uv.x * 6.0)*0.04;

    // Base layout lines (Static from provided code)
    float wBase = 0.28;
    float bBase = 0.08 + sin(uv.x * 1.5)*0.02;

    // --- Coloring and Outlines (Fluid Sky Implementation) ---
    vec3 col = vec3(0.0);
    float edgeDist = 1.0;

    if (uv.y > wBase) {
        if (uv.y < m2) {
            // Static mountain 1
            col = vec3(0.1, 0.25, 0.45);
            edgeDist = min(abs(uv.y - m2), uv.y - wBase);
        } else if (uv.y < m1) {
            // Static mountain 2
            col = vec3(0.2, 0.35, 0.6);
            edgeDist = min(abs(uv.y - m1), abs(uv.y - m2));
        } else {

            float fluidPatternVal;

            if (uv.y < s4) {
                // Sky 5: Flowing fluid near sun (Orange-Yellow mix)
                fluidPatternVal = getFluidSkyFlow(uv, 1.0, 10.0);
                col = mix(vec3(1.0, 0.85, 0.6), vec3(1.0, 0.95, 0.7), smoothstep(-0.2, 0.2, fluidPatternVal));
                edgeDist = min(abs(uv.y - s4), abs(uv.y - m1));
            } else if (uv.y < s3) {
                // Sky 4: Flowing fluid (Yellow mix)
                fluidPatternVal = getFluidSkyFlow(uv, 0.8, 8.0);
                col = mix(vec3(0.9, 0.7, 0.1), vec3(1.0, 0.8, 0.1), smoothstep(-0.2, 0.2, fluidPatternVal));
                edgeDist = min(abs(uv.y - s3), abs(uv.y - s4));
            } else if (uv.y < s2) {
                // Sky 3: Flowing fluid (Orange-Cyan mix)
                fluidPatternVal = getFluidSkyFlow(uv, 0.6, 6.0);
                col = mix(vec3(1.0, 0.4, 0.1), vec3(1.0, 0.6, 0.1), smoothstep(-0.2, 0.2, fluidPatternVal));
                edgeDist = min(abs(uv.y - s2), abs(uv.y - s3));
            } else if (uv.y < s1) {
                // Sky 2: Flowing fluid (Cyan-Deep Blue mix)
                fluidPatternVal = getFluidSkyFlow(uv, 0.4, 5.0);
                col = mix(vec3(0.1, 0.6, 0.8), vec3(0.2, 0.8, 0.9), smoothstep(-0.2, 0.2, fluidPatternVal));
                edgeDist = min(abs(uv.y - s1), abs(uv.y - s2));
            } else {
                // Sky 1: Flowing fluid top layer (Deep Blue)
                fluidPatternVal = getFluidSkyFlow(uv, 0.2, 4.0);
                col = mix(vec3(0.05, 0.3, 0.6), vec3(0.05, 0.5, 0.8), smoothstep(-0.2, 0.2, fluidPatternVal));
                edgeDist = abs(uv.y - s1);
            }
        }
    } else if (uv.y > bBase) {
        // WATER LAYERS (Preserved faster ripples and currents)
        float bands = 14.0;

        float current = sin(uv.x * 4.0 + iTime * 2.0) * 0.006;
        float ripple = noise(uv.x * 15.0 - iTime * 3.0) * 0.003;
        float waveY = uv.y - current - ripple;

        float id = floor(waveY * bands / wBase);
        float localY = fract(waveY * bands / wBase);

        float colorShift = noise(id * 0.1 + iTime * 0.5) * 0.2;
        col = mix(vec3(0.0, 0.2, 0.5), vec3(0.2, 0.6, 0.9), (id / bands) + colorShift);

        float bandDist = min(localY, 1.0 - localY) * (wBase / bands);
        edgeDist = min(bandDist, min(abs(uv.y - wBase), abs(uv.y - bBase)));
    } else {
        // BEACH LAYERS (Preserved gentle lapping)
        float bands = 3.0;
        float waveY = uv.y - sin(uv.x * 2.0 + iTime)*0.005;
        float id = floor(waveY * bands / bBase);
        float localY = fract(waveY * bands / bBase);

        col = mix(vec3(0.05), vec3(0.3, 0.25, 0.35), id / bands);
        float bandDist = min(localY, 1.0 - localY) * (bBase / bands);
        edgeDist = min(bandDist, abs(uv.y - bBase));
    }


    float lineThickness = 0.004;
    float edgeSoftness = 0.003;
    float outline = smoothstep(lineThickness, lineThickness + edgeSoftness, edgeDist);
    col = mix(vec3(0.0), col, outline);

    // Drawing only the bonsai and rock
    float sil = getSilhouettes(uv);
    float silMask = smoothstep(0.005, 0.0, sil);
    col = mix(col, vec3(0.05), silMask);

    float tipGlow = smoothstep(0.015, 0.0, abs(uv.x - drawProgress));
    col += vec3(tipGlow * 0.35);

    fragColor = vec4(col, 1.0);
}
