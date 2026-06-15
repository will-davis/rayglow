// iChannel0: milk

#define MAX_STEPS 80
#define SURF_DIST .001
#define MAX_DIST 20.

// Rotation matrix
mat2 Rot(float a) {
    float s = sin(a), c = cos(a);
    return mat2(c, -s, s, c);
}

// Smooth minimum for organic blending
float smin(float a, float b, float k) {
    float h = clamp(0.5 + 0.5 * (b - a) / k, 0.0, 1.0);
    return mix(b, a, h) - k * h * (1.0 - h);
}

// The scene distance function
float GetDist(vec3 p, float phase) {
    // Rotate the entire scene over time
    p.xy *= Rot(iTime * 0.2);
    p.xz *= Rot(iTime * 0.3);
    
    // Base sphere
    float sphere = length(p) - 2.2;
    
    // Audio-reactive warping
    // We use meta.x (phase) to create a pulsating rhythmic displacement
    float wave = sin(p.x * 3.0 + phase) * 
                 sin(p.y * 3.0 + phase * 0.8) * 
                 sin(p.z * 3.0 + phase * 1.2);
    
    // Scale the warp by a combination of time and the bass phase
    float displacement = wave * (0.2 + 0.3 * sin(phase * 0.5));
    
    // Add some crystalline structures using absolute values (folding space)
    vec3 q = abs(p) - 0.5;
    q.xy *= Rot(phase * 0.2);
    float crystals = length(max(q, 0.0)) - 0.1;
    
    // Blend the sphere and the crystals
    float scene = smin(sphere + displacement, crystals, 0.4);
    
    return scene;
}

// Simple raymarching loop
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

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
    // 1. Initialize audio data
    // meta.x = phase accumulation of sub frequency
    vec4 meta = texelFetch(iChannel0, ivec2(6, 0), 0);
    float phase = meta.x; 
    
    // Normalize coordinates (-1 to 1)
    vec2 uv = (fragCoord - 0.5 * iResolution.xy) / iResolution.y;
    
    // Camera setup
    vec3 ro = vec3(0, 0, -4); // Ray origin
    vec3 rd = normalize(vec3(uv, 1)); // Ray direction
    
    // Raymarch the scene
    float d = RayMarch(ro, rd, phase);
    
    // Coloring logic
    vec3 col = vec3(0);
    
    if(d < MAX_DIST) {
        vec3 p = ro + rd * d;
        
        // Create a "Fresnel" effect for a glowing edge
        vec3 n = normalize(vec3(
            GetDist(p + vec3(.01, 0, 0), phase) - GetDist(p - vec3(.01, 0, 0), phase),
            GetDist(p + vec3(0, .01, 0), phase) - GetDist(p - vec3(0, .01, 0), phase),
            GetDist(p + vec3(0, 0, .01), phase) - GetDist(p - vec3(0, 0, .01), phase)
        ));
        
        float fresnel = pow(1.0 - max(0.0, dot(n, -rd)), 3.0);
        
        // Dynamic color palette shifting based on phase
        vec3 colorA = vec3(0.1, 0.4, 0.9); // Deep Blue
        vec3 colorB = vec3(0.8, 0.2, 1.0); // Neon Purple
        vec3 colorC = vec3(0.2, 0.9, 0.7); // Cyan
        
        vec3 baseCol = mix(colorA, colorB, sin(phase * 0.1) * 0.5 + 0.5);
        baseCol = mix(baseCol, colorC, cos(phase * 0.2) * 0.5 + 0.5);
        
        col = baseCol * fresnel * 2.0;
        col += baseCol * 0.2; // Ambient fill
    }
    
    // Background: Cosmic Dust/Vortex
    float bgWave = length(uv) * 2.0 - (sin(phase * 0.5) * 0.5);
    col += vec3(0.05, 0.02, 0.1) / (bgWave + 0.1);
    
    // Add a "bass flash" to the overall scene
    float flash = smoothstep(5.0, 0.0, abs(sin(phase)));
    col += vec3(0.1, 0.05, 0.2) * flash;

    // Post-processing: Vignette and Gamma correction
    col *= 1.0 - length(uv) * 0.5;
    col = pow(col, vec3(0.8)); 
    
    fragColor = vec4(col, 1.0);
}
