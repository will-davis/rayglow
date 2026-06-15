/* 
    Kinetic Neon Ribbon Visualizer
    Optimized for 8:1 Aspect Ratio
    Control: meta.x (Phase accumulation of sub-frequencies)
*/

// iChannel0: milk
#define PI 3.14159265359
#define GLOW_INTENSITY 0.4
#define RIBBON_COUNT 6.0

// Helper to create a color palette
vec3 palette(float t) {
    // High contrast neon colors: Deep Purple, Electric Blue, Hot Pink, Gold
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

void mainImage( out vec4 fragColor, in vec2 fragCoord ) {
    // Normalize coordinates
    // We divide by iResolution.y to keep the scale consistent regardless of width
    vec2 uv = (fragCoord - 0.5 * iResolution.xy) / iResolution.y;
    
    // Fetch the sub-frequency phase accumulation
    vec4 meta = texelFetch(iChannel0, ivec2(6, 0), 0);
    vec4 sub = texelFetch(iChannel0, ivec2(4, 0), 0);
    vec4 vol   = texelFetch(iChannel0, ivec2(3, 0), 0);
    float phase = meta.x;
    
    // Create a slow time-based drift for variety
    float time = iTime * 0.5;
    
    // We want the visual to flow horizontally. 
    // We shift the UVs based on the phase to create the "streaming" effect.
    vec2 uv0 = uv;
    
    // Domain Warping: This creates the organic "liquid" feel
    // We use phase to drive the speed of the warp
    for(float i = 1.0; i < 8.0; i++) {
        uv0.x += 0.3 / i * sin(i * 3.0 * uv0.y + phase * 0.2 + time);
        uv0.y += 0.3 / i * cos(i * 3.0 * uv0.x + phase * 0.2 + time);
    }

    // Base composition: Creating multiple glowing ribbons
    float finalComposition = 0.0;
    
    for(float i = 0.0; i < RIBBON_COUNT; i++) {
        // Offset each ribbon's position and frequency
        float offset = i * (PI * 2.0 / RIBBON_COUNT);
        
        // The core wave equation
        // phase drives the movement, uv.x spreads it across the wide screen
        float wave = sin(uv.x * 1.5 + phase * 1.5 + i) * 0.3;
        wave += sin(uv.x * 4.0 - phase * 1.8 + iTime) * 0.1;
        // Distance from the current pixel to the calculated wave line
        float dist = abs(uv.y - (wave * 1.2) * (sub.w + 0.4));
        
        // Create a sharp core and a soft glow
        float core = (0.401 / smoothstep(0.05, 0.11, dist) * .01); 
        float glow = 0.01 / (dist * dist  * 2.2 - 0.15);
	// float glow = dist * dist * .01;
        
        // Combine and modulate by a sine of the phase for a "pulsing" breath effect
        float pulse = sub.w * 2. + .2 + sub.w / 2.5; //+ sin(phase * 0.9);
        finalComposition += (core + glow) * pulse;
        // fragColor = vec4(vec3(core), 1.0); return;
    }

    // Color mapping
    // The color shifts based on the x-position and the phase
    vec3 col = palette(uv.x * 0.1 + phase * 0.05 + time * 0.1);
    
    // Final color assembly
    vec3 finalColor = col * finalComposition;
    
    // Add a subtle vignette to prevent the edges of the 8:1 screen from feeling too harsh
    float vignette = 1.0 - smoothstep(0.9, 1.5, length(uv * vec2(0.1, 1.0)));
    finalColor *= vignette;
    
    // Add a slight "digital" grain/noise for texture
    float noise = fract(sin(dot(uv, vec2(12.9898, 78.233))) * 43758.5453);
    finalColor += noise * 0.02;

    // Contrast and Gamma correction
    finalColor = pow(finalColor, vec3(0.9));
    
    fragColor = vec4(finalColor, 1.0);
}
