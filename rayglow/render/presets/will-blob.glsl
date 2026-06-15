#define MAX_STEPS 80
#define SURF_DIST .001
#define MAX_DIST 20.

mat2 Rot(float a) {
    float s = sin(a), c = cos(a);
    return mat2(c, -s, s, c);
}

float smin(float a, float b, float k) {
    float h = clamp(0.5 + 0.5 * (b - a) / k, 0.0, 1.0);
    return mix(b, a, h) - k * h * (1.0 - h);
}

float GetDist(vec3 p, float phase) {
    p.xy *= Rot(iTime * 0.2);
    p.xz *= Rot(iTime * 0.3);
    float sphere = length(p) - 2.1;
    float wave = sin(p.x * 3.0 + phase) * 
                 sin(p.y * 3.0 + phase * 0.8) * 
                 sin(p.z * 3.0 + phase * 1.2);
    float displacement = wave * (0.2 + 0.3 * sin(phase * 0.5));
    vec3 q = abs(p) - 0.5;
    q.xy *= Rot(phase * 0.2);
    float crystals = length(max(q, 0.0)) - 0.1;
    float scene = smin(sphere + displacement, crystals, 0.4);
    return scene;
}

float RayMarch(vec3 ro, vec3 rd, float phase) {
    float dO = 0.0;
    for(int i=0; i<MAX_STEPS; i++) {
        vec3 p = ro + rd * dO;
        float dS = GetDist(p, phase);
        dO += dS;
        if(dO > MAX_DIST || abs(dS) < SURF_DIST) break;
    }
    return dO;
}

vec3 basePalette( float iTime) {
    vec3 a = vec3(0.3, 0.3, 0.5);
    vec3 b = vec3(-0.3, 0.3, 0.3);
    vec3 c = vec3(1.0, 1.0, 1.0);
    vec3 d = vec3(1.8, -1.0, 0.9);
    return a + b*cos( 6.28318*(c*iTime+d) );
}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
    float phase = sin(iTime);
    float audio  = texelFetch( iChannel0, ivec2(.7,0), 0 ).x;
    vec2 uv = (fragCoord - 0.5 * iResolution.xy) / iResolution.y;
    vec3 ro = vec3(0, 0, -4);
    vec3 rd = normalize(vec3(uv, 1));
    float d = RayMarch(ro, rd, iTime);
    d = d / 1.;
    vec3 col = vec3(0);
    if(d < MAX_DIST) {
        vec3 p = ro + rd * d;
        vec3 n = normalize(vec3(
            GetDist(p + vec3(.01, 0, 0), iTime) - GetDist(p - vec3(.01, 0, 0), iTime),
            GetDist(p + vec3(0, .01, 0), iTime) - GetDist(p - vec3(0, .01, 0), iTime),
            GetDist(p + vec3(0, 0, .01), iTime) - GetDist(p - vec3(0, 0, .01), iTime)
        ));
        
        float fresnel = pow(sin(iTime) / 2.5 + 1.5 - max(0.0, dot(n, -rd)), 3.0);
        vec3 baseCol = basePalette(d + iTime / 8.);
        col = baseCol * fresnel * 2.0;
        col += baseCol - 1.;
        // baseCol -= 1.0;
    }
    // float bgWave = length(uv) * 2.0 * (sin(iTime * 0.5) * 2.5);
    // bgWave = bgWave;
    // col += vec3(0.05, 0.02, 0.1) / (bgWave + 0.6);
    // float flash = smoothstep(5.0, 0.0, phase * 3.);
    // col += vec3(0.1, 0.05, 0.2) * flash;
    col *= 1.0 - length(uv) * 0.2;
    col = pow(col, vec3(0.2));
    fragColor = vec4(col, 1.0);
}
