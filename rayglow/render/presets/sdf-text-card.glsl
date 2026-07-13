// sdf-text-card.glsl — REFERENCE CARD: bitmap signed-distance-field text.
//
// PURPOSE
//   A worked, self-contained example of rendering crisp text on the LED wall
//   from a BMFont signed-distance-field (SDF) atlas.  Renders the word
//   "RAYGLOW" with an antialiased fill + outline, the color pulsed by bass so
//   it doubles as a live preset.  Swap the GLYPHS array (see "AUTHORING") to
//   render any string.
//
//   Source font/atlas: davidlyons/text-sdf-bitmap  (Roboto Bold, Hiero SDF).
//   Assets live in presets/fonts/ — only the .png is needed at runtime; the
//   .json/.fnt are kept so you can regenerate metrics for other strings.
//
// ============================================================================
//  WHY SDF TEXT (the concept)
// ----------------------------------------------------------------------------
//  A normal bitmap font stores each glyph as opaque pixels: scale it up and you
//  get stair-stepped, blurry edges.  An SDF atlas instead stores, per texel, the
//  *signed distance to the nearest glyph edge*, remapped to 0..1: 0.5 == exactly
//  on the edge, >0.5 inside the glyph, <0.5 outside.  Because distance is a
//  smooth, ~linear field, GL's bilinear filtering interpolates it cleanly at any
//  scale.  We recover a hard-but-antialiased edge in the shader with a single
//  smoothstep() around 0.5.  One 512x512 atlas → razor-sharp text at any size,
//  plus near-free outlines/glows by thresholding at other distances.
//
//  In THIS atlas the distance field is stored in the ALPHA channel (RGB is an
//  unused flat mask), so we sample `.a`.  The renderer uploads images already
//  vertically flipped to match Shadertoy's bottom-left origin, while BMFont
//  metrics are top-down — so every atlas lookup flips Y: uv.y = 1.0 - y/H.
// ============================================================================
//
//  iChannel0: milk
//  iChannel1: fonts/roboto-bold.png
//
//  (iChannel0 = audio scalars, color reactivity only; iChannel1 = the SDF
//   atlas, REQUIRED.)  Loaded automatically by the directives above; no
//   --channel flags needed.  The directive parser reads to end-of-line, so the
//   spec must stand alone — keep trailing comments off the iChannelN lines.
//  The atlas binds with LINEAR filtering (render/passes.make_texture default) —
//  that bilinear interpolation IS what makes the field smooth. NEAREST = jaggies.

const vec2 PANEL = vec2(256.0, 64.0);     // logical wall (config.WALL_WIDTH/HEIGHT)
const float ATLAS = 512.0;                // atlas is 512x512 (common.scaleW/H)

// --- SDF edge controls (distance-field units, 0.5 == the glyph edge) ---------
const float FILL_EDGE = 0.50;   // threshold for the solid fill
const float OUTLINE   = 0.34;   // lower threshold → outline extends outward
const float SOFT      = 0.07;   // smoothstep half-width: bigger = softer/blurrier

const vec3 FILL_COL    = vec3(0.10, 0.95, 0.75);   // teal glyph body
const vec3 OUTLINE_COL = vec3(0.02, 0.05, 0.12);   // near-black halo
const vec3 BG_COL      = vec3(0.0);

// ============================================================================
//  THE WHOLE FONT  —  copy this block, then edit STR[]/LEN to set the text
// ----------------------------------------------------------------------------
//  Every printable-ASCII glyph (codes 32..126) of Roboto Bold, baked straight
//  from roboto-bold.json so future-you never has to touch JSON again.  Indexed
//  by ASCII code minus 32:  FONT_RECT[c - 32] is the box for character c.
//      FONT_RECT[i] = vec4(x, y, width, height)   — glyph box in the atlas (px)
//      FONT_META[i] = vec3(xoffset, yoffset, xadvance)
//          xoffset/yoffset : placement of the box relative to the pen,
//                            measured top-down from the line top
//          xadvance        : how far the pen moves to the next glyph
//
//  TO RENDER YOUR OWN TEXT: leave both tables untouched and edit only STR[]
//  and LEN (just below the tables) — list each character's ASCII code; the
//  inline comment in each table row shows you which code is which glyph.
//  Lowercase, digits and punctuation all work; ' ' (32) is a real spacer.
// ----------------------------------------------------------------------------
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
const float LINE_H = 92.0;    // common.lineHeight — full glyph cell height (px)

// --- The text to display, as ASCII codes (each indexes FONT_* via code-32) ---
//  "RAYGLOW" — the original demo.  Edit these to print anything; e.g.
//  "Hi!" would be int[3](72, 105, 33).  Keep LEN == the array length.
const int LEN = 7;
const int STR[7] = int[7](
    82,   // R
    65,   // A
    89,   // Y
    71,   // G
    76,   // L
    79,   // O
    87    // W
);

// Sample the SDF for one glyph (gi = ASCII code - 32), at point `tp` in the
// glyph's local atlas-pixel space (origin = top-left of its box, y downward).
// Returns the distance value (0.5 == edge); 0 outside the box so it never draws.
float glyphDist(int gi, vec2 tp) {
    vec4 r = FONT_RECT[gi];
    if (tp.x < 0.0 || tp.y < 0.0 || tp.x > r.z || tp.y > r.w) return 0.0;
    // atlas texel (top-down) → normalized uv, flipping Y for the GL upload.
    vec2 uv = vec2((r.x + tp.x) / ATLAS,
                   1.0 - (r.y + tp.y) / ATLAS);
    return texture(iChannel1, uv).a;
}

void mainImage(out vec4 O, in vec2 I) {
    // Quantize to the real panel grid so the dry-run GIF matches the wall.
    vec2 px = floor(I / iResolution.xy * PANEL);

    // --- Fit the whole word into the panel ----------------------------------
    // Total advance width of the string in font pixels...
    float textW = 0.0;
    for (int i = 0; i < LEN; i++) textW += FONT_META[STR[i] - 32].z;
    // ...choose a uniform scale so it spans ~88% of the wall width, then center.
    float scale  = (PANEL.x * 0.88) / textW;
    float originX = (PANEL.x - textW * scale) * 0.5;     // left pen, in panel px
    // Vertically center the LINE_H cell on the panel.
    float originY = (PANEL.y - LINE_H * scale) * 0.5;

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
        pen += FONT_META[gi].z;                       // advance to next glyph
    }

    // --- Distance field → pixels --------------------------------------------
    // smoothstep gives ~1px antialiasing for free; widen SOFT for a soft glow.
    float fill    = smoothstep(FILL_EDGE - SOFT, FILL_EDGE + SOFT, dist);
    float outline = smoothstep(OUTLINE  - SOFT, OUTLINE  + SOFT, dist);

    // Bass pulse: shift the fill toward white on kicks (live audio only).
    float bass = texelFetch(iChannel0, ivec2(0, 0), 0).w;   // .w = ~125ms envelope
    vec3 fillCol = mix(FILL_COL, vec3(1.0), clamp(bass - 0.6, 0.0, 0.6));

    // Composite: background → outline halo → glyph fill.
    vec3 col = mix(BG_COL, OUTLINE_COL, outline);
    col = mix(col, fillCol, fill);

    O = vec4(col, 1.0);
}
