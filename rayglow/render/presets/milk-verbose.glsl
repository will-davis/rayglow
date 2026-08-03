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
// iChannel1: fonts/roboto-bold.png

#define SCALE       3.0   // level bars: full height = this many "typicals"
#define ONSET_SCALE 6.0   // onset bars: hits usually land 2-4

const float FILL_EDGE = 0.55;   // threshold for the solid fill
const float OUTLINE   = 0.50;   // lower threshold → outline extends outward
const float SOFT      = 0.55;   // smoothstep half-width: bigger = softer/blurrier

const vec3 FILL_COL    = vec3(0.50, 0.50, 0.50);   // teal glyph body
const vec3 OUTLINE_COL = vec3(0.10, 0.10, 0.00);   // near-black halo
const vec3 BG_COL      = vec3(0.0, 0.0, 0.0);

const vec4 FONT_RECT[95] = vec4[95](
    vec4(  0.0,   0.0,  0.0,  0.0),   // ' ' (32)
    vec4(487.0, 152.0, 19.0, 63.0),   // '!' (33)
    vec4(218.0, 453.0, 27.0, 28.0),   // '"' (34)
    vec4(270.0, 341.0, 49.0, 62.0),   // '#' (35)
    vec4(194.0,   0.0, 45.0, 80.0),   // '$' (36)
    vec4(330.0, 152.0, 58.0, 64.0),   // '%' (37)
    vec4(388.0, 152.0, 55.0, 64.0),   // '&' (38)
    vec4(496.0, 279.0, 15.0, 28.0),   // ''' (39)
    vec4(  0.0,   0.0, 28.0, 85.0),   // '(' (40)
    vec4( 28.0,   0.0, 29.0, 85.0),   // ')' (41)
    vec4( 79.0, 453.0, 40.0, 40.0),   // '*' (42)
    vec4(463.0, 403.0, 45.0, 46.0),   // '+' (43)
    vec4(198.0, 453.0, 20.0, 30.0),   // ',' (44)
    vec4(442.0, 453.0, 28.0, 16.0),   // '-' (45)
    vec4(422.0, 453.0, 20.0, 19.0),   // '.' (46)
    vec4(465.0,   0.0, 36.0, 67.0),   // '/' (47)
    vec4(285.0, 152.0, 45.0, 64.0),   // '0' (48)
    vec4( 49.0, 216.0, 31.0, 63.0),   // '1' (49)
    vec4( 80.0, 216.0, 46.0, 63.0),   // '2' (50)
    vec4(104.0, 152.0, 46.0, 64.0),   // '3' (51)
    vec4(177.0, 341.0, 47.0, 62.0),   // '4' (52)
    vec4(126.0, 216.0, 44.0, 63.0),   // '5' (53)
    vec4(150.0, 152.0, 46.0, 64.0),   // '6' (54)
    vec4(224.0, 341.0, 46.0, 62.0),   // '7' (55)
    vec4(196.0, 152.0, 45.0, 64.0),   // '8' (56)
    vec4(241.0, 152.0, 44.0, 64.0),   // '9' (57)
    vec4(482.0, 341.0, 20.0, 50.0),   // ':' (58)
    vec4(481.0,  85.0, 21.0, 64.0),   // ';' (59)
    vec4(  0.0, 453.0, 39.0, 43.0),   // '<' (60)
    vec4(157.0, 453.0, 41.0, 32.0),   // '=' (61)
    vec4( 39.0, 453.0, 40.0, 43.0),   // '>' (62)
    vec4(170.0, 216.0, 42.0, 63.0),   // '?' (63)
    vec4(239.0,   0.0, 70.0, 78.0),   // '@' (64)
    vec4(212.0, 216.0, 59.0, 62.0),   // 'A' (65)
    vec4(271.0, 216.0, 48.0, 62.0),   // 'B' (66)
    vec4(377.0,  85.0, 52.0, 64.0),   // 'C' (67)
    vec4(319.0, 216.0, 50.0, 62.0),   // 'D' (68)
    vec4(369.0, 216.0, 45.0, 62.0),   // 'E' (69)
    vec4(414.0, 216.0, 44.0, 62.0),   // 'F' (70)
    vec4(429.0,  85.0, 52.0, 64.0),   // 'G' (71)
    vec4(458.0, 216.0, 53.0, 62.0),   // 'H' (72)
    vec4(  0.0, 279.0, 19.0, 62.0),   // 'I' (73)
    vec4(443.0, 152.0, 44.0, 63.0),   // 'J' (74)
    vec4( 19.0, 279.0, 52.0, 62.0),   // 'K' (75)
    vec4( 71.0, 279.0, 43.0, 62.0),   // 'L' (76)
    vec4(114.0, 279.0, 66.0, 62.0),   // 'M' (77)
    vec4(180.0, 279.0, 52.0, 62.0),   // 'N' (78)
    vec4(  0.0, 152.0, 54.0, 64.0),   // 'O' (79)
    vec4(232.0, 279.0, 50.0, 62.0),   // 'P' (80)
    vec4(309.0,   0.0, 54.0, 74.0),   // 'Q' (81)
    vec4(282.0, 279.0, 50.0, 62.0),   // 'R' (82)
    vec4( 54.0, 152.0, 50.0, 64.0),   // 'S' (83)
    vec4(332.0, 279.0, 52.0, 62.0),   // 'T' (84)
    vec4(  0.0, 216.0, 49.0, 63.0),   // 'U' (85)
    vec4(384.0, 279.0, 57.0, 62.0),   // 'V' (86)
    vec4(  0.0, 341.0, 72.0, 62.0),   // 'W' (87)
    vec4(441.0, 279.0, 55.0, 62.0),   // 'X' (88)
    vec4( 72.0, 341.0, 55.0, 62.0),   // 'Y' (89)
    vec4(127.0, 341.0, 50.0, 62.0),   // 'Z' (90)
    vec4( 57.0,   0.0, 24.0, 85.0),   // '[' (91)
    vec4(  0.0,  85.0, 42.0, 67.0),   // '\' (92)
    vec4( 81.0,   0.0, 23.0, 85.0),   // ']' (93)
    vec4(119.0, 453.0, 38.0, 34.0),   // '^' (94)
    vec4(470.0, 453.0, 41.0, 16.0),   // '_' (95)
    vec4(396.0, 453.0, 26.0, 19.0),   // '`' (96)
    vec4(350.0, 341.0, 44.0, 50.0),   // 'a' (97)
    vec4(377.0,   0.0, 44.0, 67.0),   // 'b' (98)
    vec4(394.0, 341.0, 43.0, 50.0),   // 'c' (99)
    vec4(421.0,   0.0, 44.0, 67.0),   // 'd' (100)
    vec4(437.0, 341.0, 45.0, 50.0),   // 'e' (101)
    vec4( 42.0,  85.0, 33.0, 66.0),   // 'f' (102)
    vec4(180.0,  85.0, 45.0, 65.0),   // 'g' (103)
    vec4( 75.0,  85.0, 42.0, 66.0),   // 'h' (104)
    vec4(225.0,  85.0, 19.0, 65.0),   // 'i' (105)
    vec4(104.0,   0.0, 30.0, 81.0),   // 'j' (106)
    vec4(117.0,  85.0, 45.0, 66.0),   // 'k' (107)
    vec4(162.0,  85.0, 18.0, 66.0),   // 'l' (108)
    vec4( 88.0, 403.0, 66.0, 49.0),   // 'm' (109)
    vec4(154.0, 403.0, 42.0, 49.0),   // 'n' (110)
    vec4(  0.0, 403.0, 46.0, 50.0),   // 'o' (111)
    vec4(244.0,  85.0, 44.0, 65.0),   // 'p' (112)
    vec4(288.0,  85.0, 44.0, 65.0),   // 'q' (113)
    vec4(196.0, 403.0, 30.0, 49.0),   // 'r' (114)
    vec4( 46.0, 403.0, 42.0, 50.0),   // 's' (115)
    vec4(319.0, 341.0, 31.0, 59.0),   // 't' (116)
    vec4(226.0, 403.0, 42.0, 49.0),   // 'u' (117)
    vec4(268.0, 403.0, 45.0, 48.0),   // 'v' (118)
    vec4(313.0, 403.0, 62.0, 48.0),   // 'w' (119)
    vec4(375.0, 403.0, 46.0, 48.0),   // 'x' (120)
    vec4(332.0,  85.0, 45.0, 65.0),   // 'y' (121)
    vec4(421.0, 403.0, 42.0, 48.0),   // 'z' (122)
    vec4(134.0,   0.0, 30.0, 81.0),   // '{' (123)
    vec4(363.0,   0.0, 14.0, 72.0),   // '|' (124)
    vec4(164.0,   0.0, 30.0, 81.0),   // '}' (125)
    vec4(347.0, 453.0, 49.0, 24.0)    // '~' (126)
);
const vec3 FONT_META[95] = vec3[95](
    vec3( -3.0,   0.0, 19.0),   // ' ' (32)
    vec3( -3.0,  14.0, 21.0),   // '!' (33)
    vec3( -3.0,  10.0, 25.0),   // '"' (34)
    vec3( -3.0,  14.0, 46.0),   // '#' (35)
    vec3( -3.0,   5.0, 45.0),   // '$' (36)
    vec3( -3.0,  13.0, 58.0),   // '%' (37)
    vec3( -3.0,  13.0, 51.0),   // '&' (38)
    vec3( -3.0,  10.0, 13.0),   // ''' (39)
    vec3( -3.0,   8.0, 27.0),   // '(' (40)
    vec3( -3.0,   8.0, 28.0),   // ')' (41)
    vec3( -3.0,  14.0, 35.0),   // '*' (42)
    vec3( -3.0,  24.0, 43.0),   // '+' (43)
    vec3( -3.0,  60.0, 19.0),   // ',' (44)
    vec3( -3.0,  41.0, 30.0),   // '-' (45)
    vec3( -3.0,  57.0, 23.0),   // '.' (46)
    vec3( -3.0,  14.0, 29.0),   // '/' (47)
    vec3( -3.0,  13.0, 45.0),   // '0' (48)
    vec3( -3.0,  13.0, 45.0),   // '1' (49)
    vec3( -3.0,  13.0, 45.0),   // '2' (50)
    vec3( -3.0,  13.0, 45.0),   // '3' (51)
    vec3( -3.0,  14.0, 45.0),   // '4' (52)
    vec3( -3.0,  14.0, 45.0),   // '5' (53)
    vec3( -3.0,  13.0, 45.0),   // '6' (54)
    vec3( -3.0,  14.0, 45.0),   // '7' (55)
    vec3( -3.0,  13.0, 45.0),   // '8' (56)
    vec3( -3.0,  13.0, 45.0),   // '9' (57)
    vec3( -3.0,  26.0, 22.0),   // ':' (58)
    vec3( -3.0,  26.0, 20.0),   // ';' (59)
    vec3( -3.0,  28.0, 40.0),   // '<' (60)
    vec3( -3.0,  32.0, 45.0),   // '=' (61)
    vec3( -3.0,  28.0, 40.0),   // '>' (62)
    vec3( -3.0,  13.0, 39.0),   // '?' (63)
    vec3( -3.0,  16.0, 70.0),   // '@' (64)
    vec3( -3.0,  14.0, 52.0),   // 'A' (65)
    vec3( -3.0,  14.0, 50.0),   // 'B' (66)
    vec3( -3.0,  13.0, 51.0),   // 'C' (67)
    vec3( -3.0,  14.0, 51.0),   // 'D' (68)
    vec3( -3.0,  14.0, 44.0),   // 'E' (69)
    vec3( -3.0,  14.0, 43.0),   // 'F' (70)
    vec3( -3.0,  13.0, 53.0),   // 'G' (71)
    vec3( -3.0,  14.0, 55.0),   // 'H' (72)
    vec3( -3.0,  14.0, 23.0),   // 'I' (73)
    vec3( -3.0,  14.0, 44.0),   // 'J' (74)
    vec3( -3.0,  14.0, 50.0),   // 'K' (75)
    vec3( -3.0,  14.0, 42.0),   // 'L' (76)
    vec3( -3.0,  14.0, 68.0),   // 'M' (77)
    vec3( -3.0,  14.0, 55.0),   // 'N' (78)
    vec3( -3.0,  13.0, 54.0),   // 'O' (79)
    vec3( -3.0,  14.0, 50.0),   // 'P' (80)
    vec3( -3.0,  13.0, 54.0),   // 'Q' (81)
    vec3( -3.0,  14.0, 50.0),   // 'R' (82)
    vec3( -3.0,  13.0, 48.0),   // 'S' (83)
    vec3( -3.0,  14.0, 48.0),   // 'T' (84)
    vec3( -3.0,  14.0, 51.0),   // 'U' (85)
    vec3( -3.0,  14.0, 51.0),   // 'V' (86)
    vec3( -3.0,  14.0, 68.0),   // 'W' (87)
    vec3( -3.0,  14.0, 50.0),   // 'X' (88)
    vec3( -3.0,  14.0, 48.0),   // 'Y' (89)
    vec3( -3.0,  14.0, 47.0),   // 'Z' (90)
    vec3( -3.0,   4.0, 22.0),   // '[' (91)
    vec3( -3.0,  14.0, 33.0),   // '\' (92)
    vec3( -3.0,   4.0, 22.0),   // ']' (93)
    vec3( -3.0,  14.0, 34.0),   // '^' (94)
    vec3( -3.0,  69.0, 35.0),   // '_' (95)
    vec3( -3.0,  10.0, 26.0),   // '`' (96)
    vec3( -3.0,  27.0, 42.0),   // 'a' (97)
    vec3( -3.0,  10.0, 44.0),   // 'b' (98)
    vec3( -3.0,  27.0, 41.0),   // 'c' (99)
    vec3( -3.0,  10.0, 44.0),   // 'd' (100)
    vec3( -3.0,  27.0, 42.0),   // 'e' (101)
    vec3( -3.0,  10.0, 28.0),   // 'f' (102)
    vec3( -3.0,  27.0, 45.0),   // 'g' (103)
    vec3( -1.0,  10.0, 44.0),   // 'h' (104)
    vec3(  0.0,  11.0, 21.0),   // 'i' (105)
    vec3( -8.0,  12.0, 20.0),   // 'j' (106)
    vec3( -3.0,  10.0, 42.0),   // 'k' (107)
    vec3( -1.0,  10.0, 21.0),   // 'l' (108)
    vec3( -3.0,  27.0, 68.0),   // 'm' (109)
    vec3( -1.0,  27.0, 44.0),   // 'n' (110)
    vec3( -3.0,  27.0, 44.0),   // 'o' (111)
    vec3( -3.0,  27.0, 44.0),   // 'p' (112)
    vec3( -3.0,  27.0, 44.0),   // 'q' (113)
    vec3( -3.0,  27.0, 28.0),   // 'r' (114)
    vec3( -3.0,  27.0, 40.0),   // 's' (115)
    vec3( -6.0,  18.0, 26.0),   // 't' (116)
    vec3( -3.0,  28.0, 44.0),   // 'u' (117)
    vec3( -5.0,  28.0, 39.0),   // 'v' (118)
    vec3( -3.0,  28.0, 57.0),   // 'w' (119)
    vec3( -3.0,  28.0, 40.0),   // 'x' (120)
    vec3( -3.0,  28.0, 39.0),   // 'y' (121)
    vec3( -3.0,  28.0, 40.0),   // 'z' (122)
    vec3( -3.0,   9.0, 26.0),   // '{' (123)
    vec3( -3.0,  14.0, 20.0),   // '|' (124)
    vec3( -3.0,   9.0, 26.0),   // '}' (125)
    vec3( -3.0,  37.0, 51.0)    // '~' (126)
);

const int LEN = 10;
const int STR[10] = int[10](
    48,   // 0
    49,   // 1
    50,   // 2
    51,   // 3
    52,   // 4
    53,   // 5
    54,   // 6
    55,   // 7
    56,   // 8
    57    // 9
);

// Extra horizontal spacing (in font pixels) between each character.
// 0.0 = use the font's built-in advance only; increase to add gaps.
const float CHAR_GAP = 44.0;

const vec2 PANEL = vec2(256.0, 64.0);
const float TAU = 6.2831853;
const float ATLAS = 512.0;                // atlas is 512x512 (common.scaleW/H)
const float LINE_H = 92.0;    // common.lineHeight — full glyph cell height (px)

float glyphDist(int gi, vec2 tp) {
    vec4 r = FONT_RECT[gi];
    if (tp.x < 0.0 || tp.y < 0.0 || tp.x > r.z || tp.y > r.w) return 0.0;
    // atlas texel (top-down) → normalized uv, flipping Y for the GL upload.
    vec2 uv = vec2((r.x + tp.x) / ATLAS,
                   1.0 - (r.y + tp.y) / ATLAS);
    return texture(iChannel1, uv).a;
}

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

        // Initialize output; text renders as the base layer, bars draw on top.
        O = vec4(0.,0.,0.,1.0);

    // --- Fit the whole word into the panel ----------------------------------
       // Total advance width of the string in font pixels, plus inter-character gap...
    float textW = 0.0;
        for (int i = 0; i < LEN; i++) textW += FONT_META[STR[i] - 32].z;
        textW += (float(LEN) - 1.0) * CHAR_GAP;
    // ...choose a uniform scale so it spans ~88% of the wall width, then center.
    float scale  = (PANEL.x * 0.98) / textW;
    float originX = (PANEL.x - textW * scale) * 0.5;     // left pen, in panel px
    // Vertically center the LINE_H cell on the panel.
    float originY = (PANEL.y - LINE_H * scale) * 0.05;

    // Map this panel pixel into font-pixel "text space" (y grows DOWNWARD,
    // matching BMFont's top-down metrics — note the flip from panel y-up).
    float tx = (px.x - originX) / scale;
    float ty = (PANEL.y - 1.0 - px.y - originY) / scale;

    // --- Walk the pen across the glyphs, accumulating the nearest field ------
    float dist = 0.0;
    float pen = 0.0;
    for (int i = 0; i < LEN; i++) {
        int gi = STR[i] - 32;                        // ASCII code → table index
        vec2 local = vec2(tx - (pen + FONT_META[gi].x),  // into glyph box, top-down
                          ty - FONT_META[gi].y);
        dist = max(dist, glyphDist(gi, local));      // glyphs don't overlap → max
               pen += FONT_META[gi].z + (i < LEN - 1 ? CHAR_GAP : 0.0); // advance + gap
    }

        // Render the SDF text as the base layer (behind everything)
                float fill    = smoothstep(FILL_EDGE - SOFT, FILL_EDGE + SOFT, dist);
                float outline = smoothstep(OUTLINE   - SOFT, OUTLINE   + SOFT, dist);
                O.rgb         =+ vec3(outline * 0.6);
                O.rgb         = mix(O.rgb, FILL_COL, fill);

    // dim line where a level bar of exactly 1.0 ("typical") would end
    if (px.y == floor(PANEL.y / SCALE) && px.x < 236.0) O.rgb = vec3(0.00);

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

