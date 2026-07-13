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
const float OUTLINE   = 0.55;   // lower threshold → outline extends outward
const float SOFT      = 0.07;   // smoothstep half-width: bigger = softer/blurrier

const vec3 FILL_COL    = vec3(0.10, 0.95, 0.75);   // teal glyph body
const vec3 OUTLINE_COL = vec3(0.0, 0.0, 0.0);   // near-black halo

vec3 palette(float t)
{
    vec3 a = vec3(0.5, 0.5, 0.5);
    vec3 b = vec3(0.5, 0.5, 0.5);
    vec3 c = vec3(1.0, 1.0, 1.0);
    vec3 d = vec3(0.0, 0.33, 0.66);
    return a + b * cos(6.28318 * (c * t + d));
}

bool bar(vec2 px, float col, float value) {
    return px.x == col && px.y < value * PANEL.y;
}

// ============================================================================
//  AUTHORING: the glyph table
// ----------------------------------------------------------------------------
//  Each glyph is one row from roboto-bold.json (BMFont metrics), in atlas
//  PIXELS.  GLSL can't read JSON, so the layout is baked in here:
//      RECT = vec4(x, y, width, height)   — the glyph's box in the atlas
//      META = vec3(xoffset, yoffset, xadvance)
//          xoffset/yoffset : where to place the box relative to the pen,
//                            measured top-down from the line top
//          xadvance        : how far the pen moves to the next glyph
//  To render a different word: pull its chars from the JSON and rebuild these
//  two arrays (a 6-line Python loop over d['chars'] — see the repo readme).
// ----------------------------------------------------------------------------
const int N = 7;                                  // glyph count in "RAYGLOW"
const vec4 RECT[7] = vec4[7](
    vec4(282.0, 279.0, 50.0, 62.0),   // R
    vec4(212.0, 216.0, 59.0, 62.0),   // A
    vec4( 72.0, 341.0, 55.0, 62.0),   // Y
    vec4(429.0,  85.0, 52.0, 64.0),   // G
    vec4( 71.0, 279.0, 43.0, 62.0),   // L
    vec4(  0.0, 152.0, 54.0, 64.0),   // O
    vec4(  0.0, 341.0, 72.0, 62.0)    // W
);
const vec3 META[7] = vec3[7](
    vec3(-3.0, 14.0, 50.0),   // R
    vec3(-3.0, 14.0, 52.0),   // A
    vec3(-3.0, 14.0, 48.0),   // Y
    vec3(-3.0, 13.0, 53.0),   // G
    vec3(-3.0, 14.0, 42.0),   // L
    vec3(-3.0, 13.0, 54.0),   // O
    vec3(-3.0, 14.0, 68.0)    // W
);
const float LINE_H = 92.0;    // common.lineHeight — full glyph cell height (px)

// Sample the SDF for one glyph, at point `tp` in the glyph's local atlas-pixel
// space (origin = top-left of the glyph's RECT, y growing downward).  Returns
// the distance value (0.5 == edge); returns 0 outside the box so it never draws.
float glyphDist(int i, vec2 tp) {
    vec4 r = RECT[i];
    if (tp.x < 0.0 || tp.y < 0.0 || tp.x > r.z || tp.y > r.w) return 0.0;
    // atlas texel (top-down) → normalized uv, flipping Y for the GL upload.
    vec2 uv = vec2((r.x + tp.x) / ATLAS,
                   1.0 - (r.y + tp.y) / ATLAS);
    return texture(iChannel1, uv).a;
}

vec3 BG_COL = vec3(0.0);

void mainImage(out vec4 O, in vec2 I) {
    // IMPORT AUDIO INFO
    vec4 bass  = texelFetch(iChannel0, ivec2(0, 0), 0);
    vec4 mid   = texelFetch(iChannel0, ivec2(1, 0), 0);
    vec4 treb  = texelFetch(iChannel0, ivec2(2, 0), 0);
    vec4 vol   = texelFetch(iChannel0, ivec2(3, 0), 0);
    vec4 sub   = texelFetch(iChannel0, ivec2(4, 0), 0);
    vec4 theta = texelFetch(iChannel0, ivec2(5, 0), 0);
    vec4 meta  = texelFetch(iChannel0, ivec2(6, 0), 0);

    // Quantize to the real panel grid so the dry-run GIF matches the wall.
    vec2 px = floor(I / iResolution.xy * PANEL);
    float radius = length(px);

    // --- Fit the whole word into the panel ----------------------------------
    // Total advance width of the string in font pixels...
    float textW = 0.0;
    for (int i = 0; i < N; i++) textW += META[i].z;
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
    for (int i = 0; i < N; i++) {
        vec2 local = vec2(tx - (pen + META[i].x),   // into glyph box, top-down
                          ty - META[i].y);
        dist = max(dist, glyphDist(i, local));      // glyphs don't overlap → max
        pen += META[i].z;                           // advance to next glyph
    }

    // --- Distance field → pixels --------------------------------------------
    // smoothstep gives ~1px antialiasing for free; widen SOFT for a soft glow.
    float fill    = smoothstep(FILL_EDGE - SOFT, FILL_EDGE + SOFT, dist);
    float outline = smoothstep(OUTLINE  - SOFT, OUTLINE  + SOFT, dist);

    // WILL COLORING
    float phaseshift = (sin(theta.z) + 3.0) / 4.0;
    vec3 fillCol = palette((sin(dist) * 1.8 + meta.x / 2.0)) * 1.0;
    // Composite: background → outline halo → glyph fill.
    vec3 col = mix(BG_COL, OUTLINE_COL, outline);
    col = mix(col, fillCol, fill);

    O = vec4(col, 1.0);
}
