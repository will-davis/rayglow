// Neon Sandstorm
// By Noztol

float rand1(vec2 p) {
    vec3 p3  = fract(vec3(p.xyx) * .1031);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}

float hash31(vec3 p3) {
    p3  = fract(p3 * .1031);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}

float Rand(vec2 p) {
    return fract(sin(dot(p.xy, vec2(12.9898, 78.233))) * 43758.5453);
}

float ValueNoise(vec2 p) {
    vec2 i = floor(p);
    vec2 f = fract(p);
    vec2 s = smoothstep(0.0, 1.0, f);
    float nx0 = mix(Rand(i + vec2(0.0, 0.0)), Rand(i + vec2(1.0, 0.0)), s.x);
    float nx1 = mix(Rand(i + vec2(0.0, 1.0)), Rand(i + vec2(1.0, 1.0)), s.x);
    return mix(nx0, nx1, s.y);
}


float terrain(vec2 p) {
    float dune1 = sin(p.x * 0.3 + sin(p.y * 0.2)) * sin(p.y * 0.25);
    float dune2 = sin(p.x * 0.5 + p.y * 0.8) * 0.5;
    return (dune1 + dune2) * 1.6 - 1.0;
}

float map(vec3 p) {
    return p.y - terrain(p.xz);
}

vec3 calcNormal(vec3 p) {
    const vec2 e = vec2(0.01, 0.0);
    vec3 n = normalize(vec3(
        map(p + e.xyy) - map(p - e.xyy),
        map(p + e.yxy) - map(p - e.yxy),
        map(p + e.yyx) - map(p - e.yyx)
    ));
    n.x += (rand1(p.xz * 300.0) - 0.5) * 0.02;
    n.z += (rand1(p.xz * 300.1) - 0.5) * 0.02;
    return normalize(n);
}

float calcShadow(vec3 ro, vec3 rd) {
    float res = 1.0;
    float t = 0.05;
    for(int i = 0; i < 20; i++) {
        float h = map(ro + rd * t);
        if(h < 0.001) return 0.1;
        res = min(res, 6.0 * h / t);
        t += h;
        if(t > 15.0) break;
    }
    return clamp(res, 0.1, 1.0);
}

float GetDustDensity(vec3 p) {
    float freq = 0.5;
    float ampl = 0.5;
    float noiseAccum = 0.0;

    // Wind blowing fast
    vec2 scroll = p.xz - vec2(iTime * 8.0, iTime * 4.0);

    for (int i = 0; i < 3; ++i) {
        noiseAccum += ValueNoise(scroll * freq) * ampl;
        ampl *= 0.5;
        freq *= 2.0;
        scroll.x += iTime * 2.0;
    }

    float heightAboveGround = p.y - terrain(p.xz);
    float fog = clamp(noiseAccum * 1.5 - heightAboveGround * 0.3, 0.0, 1.0);


    vec3 windMotion = p * 15.0 - vec3(iTime * 30.0, 0.0, iTime * 15.0);
    float grit = hash31(windMotion);

    // Sharpen the grit to create distinct "specs" of sand
    float explicitDust = pow(grit, 3.0) * 2.5;

    return fog * fog * explicitDust;
}


void mainImage(out vec4 fragColor, in vec2 fragCoord) {
    vec2 uv = (fragCoord - 0.5 * iResolution.xy) / iResolution.y;

    float speed = iTime * 6.0;
    float pathX = sin(speed * 0.05) * 12.0;
    float targetPathX = sin((speed + 10.0) * 0.05) * 12.0;

    vec3 ro = vec3(pathX, 6.0 + sin(speed * 0.2) * 1.5, speed);
    vec3 ta = vec3(targetPathX, 1.0, speed + 10.0);

    vec2 drag = iMouse.z > 0.0 ? (iMouse.xy / iResolution.xy - 0.5) : vec2(0.0, -0.15);
    ta.x -= drag.x * 15.0;
    ta.y -= drag.y * 15.0;

    float roll = -cos(speed * 0.05) * 0.3;

    vec3 ww = normalize(ta - ro);
    vec3 uu = normalize(cross(ww, vec3(sin(roll), cos(roll), 0.0)));
    vec3 vv = cross(uu, ww);
    vec3 rd = normalize(uv.x * uu + uv.y * vv + 2.0 * ww);

    float t = 0.1 + 0.1 * rand1(fragCoord);
    vec3 col = vec3(0.0);
    vec3 inscatteredLight = vec3(0.0);
    float transmittance = 1.0;

    float d = 0.0;
    vec3 p;

    for (int i = 0; i < 150; i++) {
        p = ro + t * rd;
        d = map(p);

        float h = terrain(p.xz);

        // Solid neon color bands mapped to world space
        vec3 neon = cos(p.z * 0.15 + p.x * 0.1 + h * 2.0 + vec3(0.0, 1.0, 2.0)) * 0.5 + 0.5;
        neon *= 0.8 + 0.2 * sin(iTime * 2.0);

        if (d < 0.5) {
            inscatteredLight += transmittance * neon * 0.003 / (abs(d) + 0.01) * exp(-t * 0.05);
        }

        float dustDensity = GetDustDensity(p);
        if (dustDensity > 0.01) {
            // Because the density is so gritty, the light scattering will look like bright sparkles
            vec3 dustColor = neon * 0.8 + vec3(0.02);

            float stepScatter = dustDensity * 0.12;
            inscatteredLight += transmittance * dustColor * stepScatter;
            transmittance *= exp(-dustDensity * 0.15); // Thicker dust blocks vision faster
        }

        if (d < 0.001 || t > 80.0 || transmittance < 0.01) break;
        t += max(0.1, d * 0.5);
    }

    if (d < 0.001 && transmittance > 0.01) {
        vec3 n = calcNormal(p);
        vec3 lightDir = normalize(vec3(0.8, 0.2, 0.4));
        float shadow = calcShadow(p, lightDir);

        vec3 albedo = vec3(0.02);
        float diff = max(dot(n, lightDir), 0.0) * shadow;
        float amb = 0.05 + 0.05 * n.y;

        vec3 viewDir = normalize(ro - p);
        vec3 halfDir = normalize(lightDir + viewDir);
        float spec = pow(max(dot(n, halfDir), 0.0), 8.0) * 0.02 * shadow;

        vec3 surfaceCol = albedo * (diff * vec3(0.2, 0.3, 0.4) + amb) + spec;

        float h = terrain(p.xz);
        vec3 neonBase = cos(p.z * 0.15 + p.x * 0.1 + h * 2.0 + vec3(0.0, 1.0, 2.0)) * 0.5 + 0.5;
        neonBase *= 0.8 + 0.2 * sin(iTime * 2.0);

        // We use smoothstep to sharpen the gradient into more solid, distinct "bands" of color
        neonBase = smoothstep(0.1, 0.9, neonBase);

        float detailFade = smoothstep(60.0, 20.0, t);

        // Apply solid color directly to the geometry
        surfaceCol += neonBase * 0.5 * detailFade;

        col = surfaceCol * transmittance;
    } else {
        col = vec3(0.01, 0.015, 0.04) * (1.0 - uv.y) * transmittance;
    }

    col += inscatteredLight;
    col = (col * (2.51 * col + 0.03)) / (col * (2.43 * col + 0.59) + 0.14);
    fragColor = vec4(col, 1.0);
}
