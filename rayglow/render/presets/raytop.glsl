// raytop.glsl — a truncated btop on the 256x64 wall: GPU / CPU / network monitor.
//
// Pairs with raytop_sender.py (run on the desktop, --host the Pi). That sender
// self-labels as telemetry (source_domain=2) and packs scalars into the v2 milk
// packet; this preset reads them back from the 'milk' texture (16x3, feed v3 —
// the v2 scalars land in the legacy/globals row 2). NO rayglow core code
// changed — only this shader knows the telemetry convention:
//   legacy bass/mid ((9,2).x/.y, EMA at (10,2).x/.y) = primary / secondary
//     fraction 0..1 (GPU util & VRAM / CPU util & RAM / net down & up) — read
//     by the bufA history pass, not here
//   milk (2,2)  .x=clock MHz  .y=used GB  .z=total GB  .w=temp C  (raw floats
//     in the centroid/flux/flatness/rolloff descriptor slots)
//   milk (3,2)  .x = metric mode id (0 gpu, 1 cpu, 2 net)  (the crest slot)
//   milk (7,2)  .z = source_domain (2 = our telemetry; anything else => demo)
//
// Layout (left waterfall ~1/3, right block ~2/3, split at mid-height):
//   +----//--+--------------+
//   |   <-   | NAME     CLK  |   left: a horizontal, centerline-MIRRORED fill
//   |  horiz | [usage graph] |   waterfall of the primary metric, scrolling left.
//   |  water +---------------+   right-top: name + clock text, btop-style scrolling
//   |  fall  | a/b GB   VRAM  |   usage graph. right-bottom: used/total text + a
//   |   <-   | [vram  graph] |   second scrolling graph (VRAM/RAM/net-up).
//   +----//--+--------------+
//
// The waterfall HISTORY lives in raytop.bufA.glsl (GPU-side scroll); this pass
// only colorizes + composites. The GPU NAME has no wire field — it's baked into
// labelTop() below; edit it for your card (raytop_sender.py prints the detected
// name at startup).
//
// iChannel0: bufA
// iChannel1: fonts/roboto-bold.png
// iChannel2: milk

const vec2 PANEL = vec2(256.0, 64.0);     // logical wall (config.WALL_WIDTH/HEIGHT)
const float ATLAS = 512.0;                // atlas is 512x512

// --- SDF edge controls (distance-field units, 0.5 == the glyph edge) ---
const float FILL_EDGE = 0.50;
const float SOFT      = 0.08;             // smoothstep half-width (small text: keep generous)

// --- Layout knobs (panel pixels) ---
const float WF_RIGHT  = 84.0;             // waterfall spans px.x 1..84
const float DIV_X     = 85.0;             // vertical divider column
const float COL_LEFT  = 88.0;             // right-block text left pen
const float COL_RIGHT = 252.0;           // right-block right edge (for right-align)
const float TXT_SCALE = 0.12;             // glyph scale (cap height ~8px)
const float TR_HEAD_TOP = 1.0;            // top header: panel-px from top to glyph cell top
const float BR_HEAD_TOP = 32.0;          // bottom header likewise

// --- Colors (LINEAR — firmware owns gamma) ---
const vec3 BORDER_COL = vec3(0.10, 0.13, 0.18);
const vec3 TEXT_COL   = vec3(0.85, 0.92, 1.00);
const vec3 TRACK_COL  = vec3(0.015, 0.02, 0.03);   // faint graph backing

const int POW10[7] = int[7](1, 10, 100, 1000, 10000, 100000, 1000000);

// ============================================================================
//  THE WHOLE FONT — verbatim from sdf-text-card.glsl. FONT_RECT[c-32] is the
//  atlas box for ASCII c; FONT_META[c-32] = (xoffset, yoffset, xadvance).
// ============================================================================
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

// Sample the SDF for one glyph (gi = ASCII code - 32). Verbatim from sdf-text-card.
float glyphDist(int gi, vec2 tp) {
    vec4 r = FONT_RECT[gi];
    if (tp.x < 0.0 || tp.y < 0.0 || tp.x > r.z || tp.y > r.w) return 0.0;
    vec2 uv = vec2((r.x + tp.x) / ATLAS, 1.0 - (r.y + tp.y) / ATLAS);
    return texture(iChannel1, uv).a;
}

// ============================================================================
//  Text building — strings are ASCII-code sequences in a fixed int[24] buffer.
// ============================================================================
int appendCode(inout int seq[24], int n, int code) {
    if (n < 24) { seq[n] = code; n++; }
    return n;
}

// Append `value` rendered with `dec` decimals (fixed-point, rounded) to seq.
int appendNumber(inout int seq[24], int n, float value, int dec) {
    value = max(value, 0.0);
    int scaled = int(floor(value * float(POW10[dec]) + 0.5));   // round, don't truncate
    int divp = POW10[dec];
    int ip = scaled / divp;             // integer part
    int fp = scaled - ip * divp;        // fractional part as integer
    int nd = 1;                          // integer-part digit count
    int t = ip;
    for (int i = 0; i < 7; i++) { if (t < 10) break; t /= 10; nd++; }
    for (int i = 0; i < 7; i++) {        // most-significant digit first
        if (i >= nd) break;
        int d = (ip / POW10[nd - 1 - i]) % 10;
        n = appendCode(seq, n, 48 + d);
    }
    if (dec > 0) {
        n = appendCode(seq, n, 46);      // '.'
        for (int i = 0; i < 7; i++) {
            if (i >= dec) break;
            int d = (fp / POW10[dec - 1 - i]) % 10;
            n = appendCode(seq, n, 48 + d);
        }
    }
    return n;
}

// Total advance width of a string (font px).
float seqWidth(int seq[24], int len) {
    float w = 0.0;
    for (int i = 0; i < 24; i++) { if (i >= len) break; w += FONT_META[seq[i] - 32].z; }
    return w;
}

// Max glyph distance at `px` for the string drawn with its left pen at penTop.x
// and its glyph-cell top penTop.y panel-px below the panel top.
float drawGlyphs(int seq[24], int len, vec2 penTop, float scale, vec2 px) {
    float tx = (px.x - penTop.x) / scale;
    float ty = (PANEL.y - 1.0 - px.y - penTop.y) / scale;   // panel y-up -> text y-down
    float dist = 0.0;
    float pen = 0.0;
    for (int i = 0; i < 24; i++) {
        if (i >= len) break;
        int gi = seq[i] - 32;
        vec2 local = vec2(tx - (pen + FONT_META[gi].x), ty - FONT_META[gi].y);
        dist = max(dist, glyphDist(gi, local));
        pen += FONT_META[gi].z;
    }
    return dist;
}

// Build the header-left label (device name) for the metric mode.
int labelTop(float mode, inout int seq[24]) {
    int n = 0;
    if (mode < 0.5) {                    // GPU — edit this for your card
        // "RTX PRO 6000"
        n = appendCode(seq, n, 82); n = appendCode(seq, n, 84); n = appendCode(seq, n, 88);
        n = appendCode(seq, n, 32);
        n = appendCode(seq, n, 80); n = appendCode(seq, n, 82); n = appendCode(seq, n, 79);
        n = appendCode(seq, n, 32);
        n = appendCode(seq, n, 54); n = appendCode(seq, n, 48);
        n = appendCode(seq, n, 48); n = appendCode(seq, n, 48);
    } else if (mode < 1.5) {             // "CPU"
        n = appendCode(seq, n, 67); n = appendCode(seq, n, 80); n = appendCode(seq, n, 85);
    } else {                             // "DOWN"
        n = appendCode(seq, n, 68); n = appendCode(seq, n, 79);
        n = appendCode(seq, n, 87); n = appendCode(seq, n, 78);
    }
    return n;
}

// Build the bottom-right label.
int labelBottom(float mode, inout int seq[24]) {
    int n = 0;
    if (mode < 0.5) {                    // "VRAM"
        n = appendCode(seq, n, 86); n = appendCode(seq, n, 82);
        n = appendCode(seq, n, 65); n = appendCode(seq, n, 77);
    } else if (mode < 1.5) {             // "RAM"
        n = appendCode(seq, n, 82); n = appendCode(seq, n, 65); n = appendCode(seq, n, 77);
    } else {                             // "UP"
        n = appendCode(seq, n, 85); n = appendCode(seq, n, 80);
    }
    return n;
}

// Append the unit string for the top-header number.
int appendUnitTop(inout int seq[24], int n, float mode) {
    if (mode < 1.5) {                    // GPU/CPU clock -> "MHZ"
        n = appendCode(seq, n, 77); n = appendCode(seq, n, 72); n = appendCode(seq, n, 90);
    } else {                             // net -> "MB"
        n = appendCode(seq, n, 77); n = appendCode(seq, n, 66);
    }
    return n;
}

// btop-ish usage ramp: green -> yellow -> red.
vec3 usageColor(float h) {
    h = clamp(h, 0.0, 1.0);
    vec3 lo = vec3(0.10, 0.85, 0.25), mid = vec3(0.95, 0.80, 0.10), hi = vec3(0.95, 0.15, 0.10);
    return h < 0.5 ? mix(lo, mid, h * 2.0) : mix(mid, hi, (h - 0.5) * 2.0);
}
// Cooler ramp for the memory / secondary graph: blue -> magenta.
vec3 memColor(float h) {
    h = clamp(h, 0.0, 1.0);
    return mix(vec3(0.10, 0.45, 0.95), vec3(0.95, 0.25, 0.75), h);
}

void mainImage(out vec4 O, in vec2 I) {
    vec2 px = floor(I / iResolution.xy * PANEL);   // true panel pixel, 0..255 / 0..63

    // --- Telemetry from the milk texture (raw floats, v3 16x3 layout) ---
    float mode  = texelFetch(iChannel2, ivec2(3, 2), 0).x;   // crest slot
    float sd    = texelFetch(iChannel2, ivec2(7, 2), 0).z;   // source_domain
    vec4  desc  = texelFetch(iChannel2, ivec2(2, 2), 0);     // .x clk .y used .z total .w temp
    float clk   = desc.x, used = desc.y, total = desc.z;

    bool demo = abs(sd - 2.0) > 0.5;     // not our telemetry (e.g. dry-run synth) -> demo values
    if (demo) { mode = 0.0; clk = 2520.0; used = 14.2; total = 32.0; }

    // History (newest at the buffer's right edge); column is vertically uniform.
    // Left waterfall + top graph read .r (primary); bottom graph reads .g (secondary).

    vec3 col = vec3(0.0);

    // ----- LEFT: centerline-mirrored fill waterfall (px.x 1..84) -----
    if (px.x >= 1.0 && px.x <= WF_RIGHT && px.y >= 1.0 && px.y <= 62.0) {
        float gu = (px.x - 1.0) / (WF_RIGHT - 1.0);          // 0 oldest .. 1 newest (right)
        float h = texture(iChannel0, vec2(gu, 0.5)).r;
        float d = abs(px.y - 31.5) / 31.5;                   // 0 center .. 1 edges
        col = TRACK_COL;
        if (d <= h) col = usageColor(h) * (1.0 - 0.35 * d);  // thickness = usage
    }

    // ----- RIGHT: two btop-style scrolling graphs -----
    if (px.x >= COL_LEFT - 1.0 && px.x <= COL_RIGHT) {
        float gu = (px.x - (COL_LEFT - 1.0)) / (COL_RIGHT - (COL_LEFT - 1.0));
        // top graph (primary), py 33..50
        if (px.y >= 33.0 && px.y <= 50.0) {
            float h = texture(iChannel0, vec2(gu, 0.5)).r;
            float vf = (px.y - 33.0) / 17.0;                 // 0 bottom .. 1 top
            col = (vf <= h) ? usageColor(h) : TRACK_COL;
        }
        // bottom graph (secondary), py 2..19
        if (px.y >= 2.0 && px.y <= 19.0) {
            float h = texture(iChannel0, vec2(gu, 0.5)).g;
            float vf = (px.y - 2.0) / 17.0;
            col = (vf <= h) ? memColor(h) : TRACK_COL;
        }
    }

    // ----- Borders / dividers (integer px -> crisp 1px lines) -----
    bool frame = (px.x == 0.0 || px.x == 255.0 || px.y == 0.0 || px.y == 63.0);
    bool divV  = (px.x == DIV_X);
    bool divH  = (px.y == 32.0 && px.x >= DIV_X);
    if (frame || divV || divH) col = BORDER_COL;

    // ----- Text (computed globally; glyphDist is 0 away from glyph boxes) -----
    int s[24];
    float td = 0.0;

    // top header: device name (left) + clock/throughput number + unit (right-aligned)
    int n = labelTop(mode, s);
    td = max(td, drawGlyphs(s, n, vec2(COL_LEFT, TR_HEAD_TOP), TXT_SCALE, px));

    n = 0;
    n = appendNumber(s, n, (mode < 2.5 && mode > 1.5) ? used : clk, (mode > 1.5) ? 1 : 0);
    n = appendUnitTop(s, n, mode);
    float wpx = seqWidth(s, n) * TXT_SCALE;
    td = max(td, drawGlyphs(s, n, vec2(COL_RIGHT - wpx, TR_HEAD_TOP), TXT_SCALE, px));

    // bottom header: used/total (or net up) number (left) + unit, and label (right)
    n = 0;
    if (mode > 1.5) {                    // net: just "<up> MB"
        n = appendNumber(s, n, total, 1);
        n = appendCode(s, n, 77); n = appendCode(s, n, 66);
    } else {                             // gpu/cpu: "<used>/<total>GB"
        n = appendNumber(s, n, used, 1);
        n = appendCode(s, n, 47);        // '/'
        n = appendNumber(s, n, total, 1);
        n = appendCode(s, n, 71); n = appendCode(s, n, 66);   // "GB"
    }
    td = max(td, drawGlyphs(s, n, vec2(COL_LEFT, BR_HEAD_TOP), TXT_SCALE, px));

    n = labelBottom(mode, s);
    wpx = seqWidth(s, n) * TXT_SCALE;
    td = max(td, drawGlyphs(s, n, vec2(COL_RIGHT - wpx, BR_HEAD_TOP), TXT_SCALE, px));

    float fill = smoothstep(FILL_EDGE - SOFT, FILL_EDGE + SOFT, td);
    col = mix(col, TEXT_COL, fill);

    O = vec4(col, 1.0);
}
