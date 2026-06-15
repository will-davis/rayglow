#define MAX_STEPS 80
#define SURFACE_DIST .001
#define MAX_DIST 20.
// iChannel0: milk
// Color Palette
vec3 palette(float t) {
    vec3 a = vec3(0.5, 0.5, 0.5);
    vec3 b = vec3(0.5, 0.5, 0.5);
    vec3 c = vec3(1.0, 1.0, 1.0);
    vec3 d = vec3(0.263, 0.416, 0.557);
    return a + b * cos(6.28318 * (c * t + d));
}

// Rotation matrix
mat2 rot(float a) {
    float s = sin(a), c = cos(a);
    return mat2(c, -s, s, c);
}

// The Fractal Scene
float map(vec3 p, float audioLow, float audioMid, float phase) {
    // Initial rotation based on time
    p.xz *= rot(iTime * 0.2);
    p.xy *= rot(iTime * 0.1);
    
    float scale = 1.0;
    float dist = 100.0;
    
    // KIFS Loop: This creates the fractal complexity
    for(int i = 0; i < 6; i++) {
        // Fold the space (Mirroring)
        p = abs(p) - vec3(0.5, 1.0, 0.5) * (1.0 + audioLow * 0.2);
        
        // Use meta.x (phase) to control the rotation of the folds
        // This ensures the "heartbeat" of the sub-bass drives the geometry
        p.xy *= rot(phase * 0.1 + float(i) * 0.5);
        p.yz *= rot(phase * 0.05 + float(i) * 0.2);
        
        // Scale and translate
        float s = 1.8 + audioMid * 0.5; 
        p *= s;
        scale *= s;
    }
    
    // Return the distance to a modified sphere/box hybrid
    return (length(p) - 2.0) / scale;
}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
    // Setup coordinates
    vec2 uv = (fragCoord - 0.5 * iResolution.xy) / iResolution.y;
    
    // --- AUDIO INPUT SECTION ---
    // Fetch the sub-frequency phase accumulation from iChannel0
    vec4 meta = texelFetch(iChannel0, ivec2(6, 0), 0);
    float phase = meta.x; 
    
    // Fetch raw frequency amplitudes from the iChannel0 texture (FFT data)
    float low = texture(iChannel0, vec2(0.1, 0.0)).r;
    float mid = texture(iChannel0, vec2(0.4, 0.0)).r;
    float high = texture(iChannel0, vec2(0.8, 0.0)).r;
    // ---------------------------

    // Camera Setup
    vec3 ro = vec3(0, 0, -3.5); // Ray origin
    vec3 rd = normalize(vec3(uv, 1.2)); // Ray direction
    
    // Dynamic camera movement
    ro.z += sin(iTime * 0.5) * 0.5;
    ro.y += cos(iTime * 0.3) * 0.5;
    
    float dO = 0.0; // Distance origin
    float glow = 0.0; // For the neon effect
    
    // Raymarching Loop
    for(int i = 0; i < MAX_STEPS; i++) {
        vec3 p = ro + rd * dO;
        float dS = map(p, low, mid, phase);
        dO += dS;
        
        // Accumulate glow based on proximity to surface
        glow += (0.01 / (dS + 0.05)) * (low + 0.2);
        
        if(dO > MAX_DIST || abs(dS) < SURFACE_DIST) break;
    }
    
    // Shading
    vec3 col = vec3(0);
    if(dO < MAX_DIST) {
        // Basic coloring based on distance and audio
        float t = dO * 0.2 + iTime * 0.1;
        col = palette(t + low);
        
        // Add fake ambient occlusion / depth shading
        col *= exp(-0.2 * dO);
    }
    
    // Mix the glow into the final color
    vec3 glowCol = palette(phase / 100. + iTime);
    col += glowCol * glow * 0.4;
    
    // Post-processing: Vignette and contrast
    col *= 1.0 - length(uv) * 0.5;
    col = pow(col, vec3(0.8)); // Gamma correction
    
    fragColor = vec4(col, 1.0);
}
