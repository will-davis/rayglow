// milk-verbose.glsl — the v3 bands, every float as its own 1-px bar.
//
// PURPOSE: a reference card, not a visualizer.  When you're building
// something and want "the punchy envelope of the kick band" or "hat
// onsets", find the bar here that moves the way you want, then copy the
// two lines of code that drew it (one texelFetch + one use of the value).
//
// HOW THE MILK TEXTURE WORKS
//   The pipeline uploads a 16x3 texture: 16 texels wide, 3 rows, each
//   texel holding 4 floats.  ".x .y .z .w" are just names for the 4 slots
//   (".rgba" are the SAME slots — two naming schemes, one vec4).
//   texelFetch reads one exact texel, no filtering, no normalized coords:
//
//       vec4 b0 = texelFetch(iChannel0, ivec2(0, 1), 0);
//                                       ^col ^row    ^mip level (always 0)
//
//   After that, b0.x is a plain float you can do math with.
//
// TEXEL MAP (this card: rows 0-1; row 2 globals are on milk-features.glsl)
//   row 0, cols 0-7 = bands b0..b7 (log-spaced 20Hz..16kHz:
//   20-60 | 60-120 | 120-250 | 250-500 | 500-1k | 1k-2.5k | 2.5k-6k |
//   6k-16k), col 8 = overall volume.  Per texel:
//     .x imm    instant level, 1.0 = "typical for this song" (hits: 2-4)
//     .y env0   ~125ms symmetric lag (the classic amplitude control)
//     .z env1   punchy flywheel: ~60ms attack / ~500ms decay — a kick
//               slams it up, it sails down
//     .w env2   heavy flywheel: ~150ms attack / ~2s decay — scene energy
//   row 1, same cols:
//     .x theta0  "music time" integrating imm (use: sin(theta0 * k))
//     .y theta1  integrates env1 — rotation speed with punchy momentum
//     .z theta2  integrates env2 — heavy momentum, slow scene motion
//     .w onset   attack strength, one-sided (kick/hat hits; ~1.0 typical)
//
// SCREEN LAYOUT (panel columns, groups left to right = b0..b7 then vol)
//   per group (13 px wide): +0 imm (band color)  +2 env0 (white)
//     +4 env1 (amber)  +6 env2 (orange)  +9..11 theta0/1/2 sawtooths
//     (fract(theta/2pi), band color dim->bright by tier)  +12 onset (cyan)
//   right margin: 240 packet age (full bar = >=1s silence), 246 live flag
//   dim row 0 strip = group label; dim line = the 1.0 "typical" level
//
// iChannel0: milk

#define SCALE       3.0   // level bars: full height = this many "typicals"
#define ONSET_SCALE 4.0   // onset bars: hits usually land 2-4

const vec2 PANEL = vec2(256.0, 64.0);
const float TAU = 6.2831853;

// Band identity colors, b0 (deep red) -> b7 (violet), vol = white.
vec3 bandColor(int g) {
    if (g == 8) return vec3(0.95);
    return 0.55 + 0.45 * cos(TAU * (float(g) / 9.0 + vec3(0.0, 0.33, 0.67)));
}

// The renderer supersamples (--scale), so gl_FragCoord counts RENDER
// pixels, not LEDs.  Dividing by iResolution and multiplying by the panel
// size gives honest 1-LED-wide columns at any --scale.
bool bar(vec2 px, float col, float value) {
    return px.x == col && px.y < clamp(value, 0.0, 1.0) * PANEL.y;
}

void mainImage(out vec4 O, in vec2 I) {
    vec2 px = floor(I / iResolution.xy * PANEL);    // panel pixel coords
    O = vec4(0.0, 0.0, 0.0, 1.0);

    // dim line where a level bar of exactly 1.0 ("typical") would end
    if (px.y == floor(PANEL.y / SCALE) && px.x < 236.0) O.rgb = vec3(0.10);

    // One group per band (cols 0-7) + vol (col 8): fetch the level texel
    // (row 0) and the motion texel (row 1), draw the same seven bars.
    for (int g = 0; g < 9; g++) {
        float x0 = 2.0 + float(g) * 27.0;           // group base column
        vec3 C = bandColor(g);
        vec4 lv = texelFetch(iChannel0, ivec2(g, 0), 0);  // imm env0 env1 env2
        vec4 mo = texelFetch(iChannel0, ivec2(g, 1), 0);  // th0 th1 th2 onset

        if (px.y == 0.0 && px.x >= x0 && px.x <= x0 + 12.0) O.rgb = C * 0.35;

        if (bar(px, x0,        lv.x / SCALE)) O.rgb = C;                    // imm
        if (bar(px, x0 +  2.0, lv.y / SCALE)) O.rgb = vec3(1.0);            // env0
        if (bar(px, x0 +  4.0, lv.z / SCALE)) O.rgb = vec3(1.0, 0.7, 0.1);  // env1
        if (bar(px, x0 +  6.0, lv.w / SCALE)) O.rgb = vec3(1.0, 0.45, 0.1); // env2

        // theta grows forever (wrapping at 200*pi), so the raw value makes
        // a useless bar.  fract(theta/2pi) = "how far into the current
        // cycle": a sawtooth that climbs faster when the band is loud.  In
        // a real visual you'd feed it to a wave or a rotation:
        //     sin(mo.x * k)      or      p *= rot(mo.y);
        // Watch theta1 after a kick — it keeps turning: that's the flywheel.
        if (bar(px, x0 +  9.0, fract(mo.x / TAU))) O.rgb = C * 0.45;   // theta0
        if (bar(px, x0 + 10.0, fract(mo.y / TAU))) O.rgb = C * 0.75;   // theta1
        if (bar(px, x0 + 11.0, fract(mo.z / TAU))) O.rgb = C;          // theta2

        if (g < 8 && bar(px, x0 + 12.0, mo.w / ONSET_SCALE))
            O.rgb = vec3(0.3, 1.0, 1.0);                               // onset
    }

    // ---- feed health ((7,2)), columns 240 & 246 -------------------------
    vec4 meta = texelFetch(iChannel0, ivec2(7, 2), 0);
    // age: seconds since the last UDP packet.  A packet normally arrives
    // every frame, so this hugs zero; a full bar means >=1s of silence.
    if (bar(px, 240.0, min(meta.x, 1.0))) O.rgb = vec3(1.0, 0.5, 0.0);
    // live: exactly 1.0 (real packets) or 0.0 (synth fallback), so this
    // bar is all-or-nothing.  Gate effects on it to react to "music
    // stopped", e.g.:  color *= mix(0.3, 1.0, meta.y);
    if (bar(px, 246.0, meta.y))           O.rgb = vec3(0.1, 0.9, 0.2);
}
