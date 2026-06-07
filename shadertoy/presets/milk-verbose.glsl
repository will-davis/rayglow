// milk-verbose.glsl — every float in the milk texture as its own 1-px bar.
//
// PURPOSE: a reference card, not a visualizer.  When you're building
// something and want "the sub envelope" or "is the feed live", find the
// bar here that moves the way you want, then copy the two lines of code
// that drew it (one texelFetch + one use of the value).
//
// HOW THE MILK TEXTURE WORKS
//   The pipeline uploads an 8x1 texture: 8 texels in a row, each holding
//   4 floats.  ".x .y .z .w" are just names for the 4 slots (".rgba" are
//   the SAME slots — two naming schemes, one vec4).  texelFetch reads one
//   exact texel, no filtering, no normalized coords:
//
//       vec4 bass = texelFetch(iChannel0, ivec2(0, 0), 0);
//                                         ^col  ^row   ^mip level (always 0)
//
//   After that, bass.x is a plain float you can do math with.
//
// TEXEL MAP (what the pipeline puts in each slot)
//   texels 0 bass, 1 mid, 2 treb, 3 vol, 4 sub — per band:
//     .x imm   instant level, 1.0 = "typical for this song" (hits: 2-4)
//     .y att   sender-smoothed level (slow swells)
//     .z ddt   d/dt of imm, SIGNED: + while rising, - while falling
//     .w env   imm through a ~125ms lag (ready-made amplitude control)
//   texel 5: integrated phase ("music time") .x bass .y mid .z treb .w vol
//   texel 6: .x sub phase   .y packet age, seconds   .z live (1 UDP, 0 synth)
//
// SCREEN LAYOUT (panel columns; band colors match milkfeed.glsl)
//   bass 8-17   mid 32-41   treb 56-65   vol 80-89   sub 104-113
//     within each group: +0 imm (band color)      +3 att (white)
//                        +6 ddt (cyan up/red dn)  +9 env (amber)
//   140-152  theta per band, drawn as fract(theta/2pi): a sawtooth that
//            climbs faster when that band is loud
//   170 packet age (full bar = >=1s silence)   176 live flag (all/nothing)
//   dim row 0 strips = group labels; dim mid line = the 1.0 "typical" level
//
// iChannel0: milk

#define SCALE     3.0     // band bars: full height = this many "typicals"
#define DDT_SCALE 10.0    // ddt bars: full height = rising this fast (1/s)

const vec3 BASS  = vec3(1.0,  0.25, 0.2);   // band identity colors
const vec3 MID   = vec3(0.2,  1.0,  0.3);
const vec3 TREB  = vec3(0.25, 0.4,  1.0);
const vec3 VOL   = vec3(0.9,  0.9,  0.9);
const vec3 SUB   = vec3(1.0,  0.2,  1.0);
const vec3 WHITE = vec3(1.0);               // att
const vec3 AMBER = vec3(1.0,  0.7,  0.1);   // env
const vec3 UP    = vec3(0.3,  1.0,  1.0);   // ddt while rising
const vec3 DOWN  = vec3(1.0,  0.3,  0.3);   // ddt while falling

// The renderer supersamples (--scale), so gl_FragCoord counts RENDER
// pixels, not LEDs.  Dividing by iResolution and multiplying by the panel
// size gives honest 1-LED-wide columns at any --scale.
const vec2 PANEL = vec2(256.0, 32.0);

// True when panel pixel `px` is inside a 1-px bar at column `col` whose
// height is `value` (0..1 of the panel; y counts up from the bottom).
bool bar(vec2 px, float col, float value) {
    return px.x == col && px.y < value * PANEL.y;
}

void mainImage(out vec4 O, in vec2 I) {
    vec2 px = floor(I / iResolution.xy * PANEL);    // panel pixel coords
    O = vec4(0.0, 0.0, 0.0, 1.0);

    // Backdrop, drawn first so the bars overwrite it:
    // row 0 = dim band-color strips marking each group,
    if (px.y == 0.0) {
        if (px.x >=   8.0 && px.x <=  17.0) O.rgb = BASS * 0.35;
        if (px.x >=  32.0 && px.x <=  41.0) O.rgb = MID  * 0.35;
        if (px.x >=  56.0 && px.x <=  65.0) O.rgb = TREB * 0.35;
        if (px.x >=  80.0 && px.x <=  89.0) O.rgb = VOL  * 0.35;
        if (px.x >= 104.0 && px.x <= 113.0) O.rgb = SUB  * 0.35;
    }
    // and a dim line where a band bar of exactly 1.0 ("typical") would end.
    if (px.y == floor(PANEL.y / SCALE) && px.x < 120.0) O.rgb = vec3(0.12);

    // Step 1: fetch each texel once, into a sensibly named vec4.
    vec4 bass  = texelFetch(iChannel0, ivec2(0, 0), 0);
    vec4 mid   = texelFetch(iChannel0, ivec2(1, 0), 0);
    vec4 treb  = texelFetch(iChannel0, ivec2(2, 0), 0);
    vec4 vol   = texelFetch(iChannel0, ivec2(3, 0), 0);
    vec4 sub   = texelFetch(iChannel0, ivec2(4, 0), 0);
    vec4 theta = texelFetch(iChannel0, ivec2(5, 0), 0);
    vec4 meta  = texelFetch(iChannel0, ivec2(6, 0), 0);

    // Step 2: one bar per float.  The pattern is always the same:
    //   if (bar(px, COLUMN, value normalized to 0..1)) O.rgb = COLOR;
    // ddt is the one oddball: it's signed, so the bar shows abs() and the
    // COLOR carries the sign (cyan = rising, red = falling).

    // ---- bass (texel 0), columns 8-17 ---------------------------------
    if (bar(px,  8.0, bass.x / SCALE))          O.rgb = BASS;   // imm
    if (bar(px, 11.0, bass.y / SCALE))          O.rgb = WHITE;  // att
    if (bar(px, 14.0, abs(bass.z) / DDT_SCALE)) O.rgb = bass.z > 0.0 ? UP : DOWN;
    if (bar(px, 17.0, bass.w / SCALE))          O.rgb = AMBER;  // env

    // ---- mid (texel 1), columns 32-41 ----------------------------------
    if (bar(px, 32.0, mid.x / SCALE))           O.rgb = MID;
    if (bar(px, 35.0, mid.y / SCALE))           O.rgb = WHITE;
    if (bar(px, 38.0, abs(mid.z) / DDT_SCALE))  O.rgb = mid.z > 0.0 ? UP : DOWN;
    if (bar(px, 41.0, mid.w / SCALE))           O.rgb = AMBER;

    // ---- treb (texel 2), columns 56-65 ---------------------------------
    if (bar(px, 56.0, treb.x / SCALE))          O.rgb = TREB;
    if (bar(px, 59.0, treb.y / SCALE))          O.rgb = WHITE;
    if (bar(px, 62.0, abs(treb.z) / DDT_SCALE)) O.rgb = treb.z > 0.0 ? UP : DOWN;
    if (bar(px, 65.0, treb.w / SCALE))          O.rgb = AMBER;

    // ---- vol (texel 3), columns 80-89 (note: vol.y is just vol.x) ------
    if (bar(px, 80.0, vol.x / SCALE))           O.rgb = VOL;
    if (bar(px, 83.0, vol.y / SCALE))           O.rgb = WHITE;
    if (bar(px, 86.0, abs(vol.z) / DDT_SCALE))  O.rgb = vol.z > 0.0 ? UP : DOWN;
    if (bar(px, 89.0, vol.w / SCALE))           O.rgb = AMBER;

    // ---- sub (texel 4), columns 104-113 — the true 23-117Hz band -------
    if (bar(px, 104.0, sub.x / SCALE))          O.rgb = SUB;
    if (bar(px, 107.0, sub.y / SCALE))          O.rgb = WHITE;
    if (bar(px, 110.0, abs(sub.z) / DDT_SCALE)) O.rgb = sub.z > 0.0 ? UP : DOWN;
    if (bar(px, 113.0, sub.w / SCALE))          O.rgb = AMBER;

    // ---- theta (texel 5 + meta.x), columns 140-152 ----------------------
    // theta grows forever (wrapping at 200*pi), so the raw value makes a
    // useless bar.  fract(theta / 2pi) = "how far into the current cycle",
    // a 0..1 sawtooth that climbs faster when the band is loud.  In a real
    // visual you'd more likely feed it to a wave: sin(theta.x * k).
    float TAU = 6.2831853;
    if (bar(px, 140.0, fract(theta.x / TAU)))   O.rgb = BASS;
    if (bar(px, 143.0, fract(theta.y / TAU)))   O.rgb = MID;
    if (bar(px, 146.0, fract(theta.z / TAU)))   O.rgb = TREB;
    if (bar(px, 149.0, fract(theta.w / TAU)))   O.rgb = VOL;
    if (bar(px, 152.0, fract(meta.x  / TAU)))   O.rgb = SUB;

    // ---- feed health (texel 6), columns 170 & 176 -----------------------
    // age: seconds since the last UDP packet.  A packet normally arrives
    // every frame, so this hugs zero; a full bar means >=1s of silence.
    if (bar(px, 170.0, min(meta.y, 1.0)))       O.rgb = vec3(1.0, 0.5, 0.0);
    // live: exactly 1.0 (real packets) or 0.0 (synth fallback), so this
    // bar is all-or-nothing.  Gate effects on it to react to "music
    // stopped", e.g.:  color *= mix(0.3, 1.0, meta.z);
    if (bar(px, 176.0, meta.z))                 O.rgb = vec3(0.1, 0.9, 0.2);
}
