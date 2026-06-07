//Obsidian Descent
//by simonsdev

#define R(a) mat2(cos(a + vec4(0,11,33,0)))

float hash(vec3 p) {
    p = fract(p * vec3(443.897, 441.423, 437.195));
    p += dot(p, p.yxz + 19.19);
    return fract((p.x + p.y) * p.z);
}

float noise(vec3 x) {
    vec3 p = floor(x), f = fract(x);
    f = f*f*(3.0-2.0*f);
    return mix(mix(mix(hash(p+vec3(0,0,0)), hash(p+vec3(1,0,0)),f.x),
                   mix(hash(p+vec3(0,1,0)), hash(p+vec3(1,1,0)),f.x),f.y),
               mix(mix(hash(p+vec3(0,0,1)), hash(p+vec3(1,0,1)),f.x),
                   mix(hash(p+vec3(0,1,1)), hash(p+vec3(1,1,1)),f.x),f.y),f.z);
}
//thank you FabriceNeyret2  - see https://www.shadertoy.com/view/ldfczS

float map(vec3 p) {
    vec3 q = p;
    p.xy *= R(p.z * 0.02 + iTime * 0.1);

    float scale = 1.0;
    float warp = sin(q.z * 0.15) * cos(q.x * 0.15 + iTime * 0.05);

    for(int i = 0; i < 4; i++) {
        p.xy *= R(0.8 + warp * 0.6 + float(i) * 0.2);
        p.xy = abs(p.xy) - (1.2 + 0.3 * sin(q.y * 0.3 + float(i)));
        p.z = abs(fract(p.z * 0.15) * 6.66 - 3.33);
        p.xz *= R(0.2 + warp * 0.4);
        p *= 1.3;
        scale *= 1.3;
    }

    float tri = max(abs(p.x) * 0.866 + p.y * 0.5, -p.y) - 1.5;
    float d = max(tri, abs(p.z) - 1.2) / scale;

    float h = clamp(0.5 + 0.5 * (d - (1.2 - length(q.xy))) / 0.5, 0.0, 1.0);
    return mix(1.2 - length(q.xy), d, h) + 0.5 * h * (1.0 - h);
}

vec3 calcN(vec3 p) {
    vec2 e = vec2(0.005, 0);
    return normalize(vec3(map(p+e.xyy)-map(p-e.xyy), map(p+e.yxy)-map(p-e.yxy), map(p+e.yyx)-map(p-e.yyx)));
}

float calcAO(vec3 p, vec3 n) {
    float occ = 0.0;
    float sca = 1.0;
    for(int i = 0; i < 5; i++) {
        float h = 0.02 + 0.1 * float(i);
        occ += (h - map(p + h * n)) * sca;
        sca *= 0.75;
    }
    return clamp(1.0 - 2.5 * occ, 0.0, 1.0);
}

void mainImage(out vec4 O, vec2 U) {
    vec2 R = iResolution.xy;
    vec2 uv = (U - 0.5 * R) / R.y;

    vec3 ro = vec3(0.0, 0.0, iTime * 4.0);
    vec3 rd = normalize(vec3(uv, 1.2));
    rd.xy *= R(sin(iTime * 0.2) * 0.4);

    float t = 0., d;
    vec3 p;

    for(int i = 0; i < 110; i++) {
        p = ro + rd * t;
        d = map(p);
        if(d < 0.001 || t > 40.) break;
        t += d * 0.5;
    }

    vec3 col = vec3(0.01, 0.01, 0.015);

    if(t < 40.) {
        vec3 n = calcN(p);
        float z = max(0.0, dot(n, -rd));
        float ao = calcAO(p, n);

        float nz = noise(p * 0.8 - vec3(0.0, 0.0, iTime * 0.4));
        float bands = abs(fract(nz * 8.0) - 0.5);

        float crease = smoothstep(0.05, 0.0, bands);
        float bleed = smoothstep(0.3, 0.0, bands);

        vec3 baseCol = vec3(0.04, 0.05, 0.07) * z * ao;
        baseCol += vec3(0.1, 0.15, 0.2) * pow(1.0 - z, 4.0) * ao;
        float spec = pow(z, 40.0) * 0.5 * ao;

        // Full color spectrum shifting over time and noise depth
        vec3 neon = 0.5 + 0.5 * cos(6.28318 * (nz * 1.5 + iTime * 0.2 + vec3(0.0, 0.333, 0.667)));

        col = baseCol + spec;
        col += neon * crease * 3.0;
        col += neon * bleed * 0.5 * ao;

        col = mix(col, vec3(0.01, 0.01, 0.015), smoothstep(15., 40., t));
    }

    O = vec4(pow(col, vec3(0.7)), 1.0);
}
