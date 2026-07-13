// milk-wave.glsl — raw waveform visualization.
//
// PURPOSE: A diagnostic view of the raw audio waveform.
//
// iChannel0: milk
// iChannel1: audio
//
// The waveform is sampled from iChannel1 at y=0.75, 
// mapping the 0..1 value to the panel height.

const vec2 PANEL = vec2(256.0, 32.0);
const vec3 WAVE_COLOR = vec3(0.3, 1.0, 0.8);

void mainImage(out vec4 O, in vec2 I) {
    vec2 px = floor(I / iResolution.xy * PANEL);
    O = vec4(0.0, 0.0, 0.0, 1.0);

    // Sample the waveform from the audio texture.
    // The waveform resides in row y=0.75 of the audio iChannel.
    float w = texture(iChannel1, vec2(px.x / PANEL.x, 0.75)).x;
    
    // Map the sampled value (0.0 to 1.0) to panel Y coordinates (0 to 31).
    float waveY = w * PANEL.y;

    // Draw the waveform line.
    // Since px.y is an integer, we check if it's the closest pixel to the wave value.
    if (abs(px.y - waveY) < 0.5) {
        O.rgb = WAVE_COLOR;
    }
    
    // Draw a dim centerline for reference (silence = 0.5).
    if (px.y == floor(PANEL.y * 0.5)) {
        O.rgb = max(O.rgb, vec3(0.1));
    }

    // Live feed indicator dot (bottom-left).
    // Red = synth fallback, Green = real UDP packets (milk (7,2).y, v3 16x3).
    if (px.x < 2.0 && px.y > PANEL.y - 3.0) {
        float live = texelFetch(iChannel0, ivec2(7, 2), 0).y;
        O.rgb = mix(vec3(0.8, 0.1, 0.1), vec3(0.1, 0.9, 0.2), live);
    }
}
