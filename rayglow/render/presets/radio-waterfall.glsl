// radio-waterfall.glsl — a classic SDR band-scan waterfall on the 256x32 panel.
//
// Pairs with sdr-pi-feed/sdr-sender.py: run that on the desktop to sweep the
// HackRF (default 2400-2500 MHz) and stream the spectrum to the Pi, then run
//   sudo ~/rgbvenv/bin/python -m rayglow.render \
//        rayglow/render/presets/radio-waterfall.glsl
// Frequency runs left (band low edge) to right (band high edge); time runs top
// (newest) to bottom (oldest); color is signal strength.
//
// Buffer A (radio-waterfall.bufA.glsl) holds and scrolls the history; this pass
// only colors it.  Run at --scale 1 for pixel-exact 256x32 rows, or leave the
// default --scale 4 for a smoother, slightly taller (≈4 s) waterfall.
//
// iChannel0 (bufA)  = scrolled 0..1 magnitude: frequency on X, time on Y.
// iChannel1 (audio) = row y=0.75 .x is the live spectrum, for the top trace.
// (Directive lines below must be bare specs — the parser reads the whole line.)
// iChannel0: bufA
// iChannel1: audio

// ===========================================================================
//  PALETTE CONTROLS — the knobs.  Hot-edit these while it runs (root re-reads
//  the file on save).  Watch sdr-sender.py --debug's "dB[min med max]" to know
//  where your floor and signals actually sit, then tune to taste.
// ===========================================================================

// --- 1. Dynamic range: which slice of the 0..1 magnitude becomes the palette.
//     Raise FLOOR to swallow more noise into black; lower CEIL to make weaker
//     traffic reach the hot colors.  Everything below FLOOR is forced black.
#define FLOOR   0.39    // magnitudes <= this -> black (noise-floor cutoff)
#define CEIL    0.99    // magnitudes >= this -> the top palette color
#define SOFT    0.05    // soft fade width just above FLOOR (0 = hard cutoff)

// --- 2. Response curve & overall level.
#define GAMMA      1.7  // >1 darkens mids (pushes signal off the floor); <1 lifts
#define BRIGHTNESS 1.00 // master output scale — drop this if it's still too bright

// --- 3. The palette itself: editable color stops.  Each stop is
//        vec4(position, R, G, B): position is where on the 0..1 (post-FLOOR/
//        CEIL/GAMMA) amplitude axis that color lands; RGB is the color there.
//        Slide a position to move where a color appears; edit RGB to recolor a
//        band.  Keep positions ascending; stop 0 at 0.0 should stay near black
//        so the quiet zones read dark.
#define USE_TURBO 0     // 1 = ignore STOPS, use the built-in Turbo rainbow instead
const int NSTOPS = 6;
const vec4 STOPS[6] = vec4[6](
    vec4(0.00, 0.00, 0.00, 0.00),   // floor      -> black
    vec4(0.18, 0.02, 0.04, 0.20),   // faint      -> deep blue
    vec4(0.42, 0.05, 0.55, 0.70),   // weak       -> cyan
    vec4(0.62, 0.10, 0.80, 0.20),   // moderate   -> green
    vec4(0.82, 1.00, 0.85, 0.10),   // strong     -> yellow
    vec4(1.00, 1.00, 0.10, 0.02));  // peak       -> red

// --- 4. Overlays.
#define SHOW_GRID   0    // faint vertical frequency gridlines (1/8-band ticks)
#define GRID_LEVEL  0.06 // gridline brightness (0 = off; lower keeps the floor dark)
#define SHOW_TRACE  0    // bright live-spectrum trace across the top edge

// ===========================================================================

// Interpolate the editable color-stop ramp at amplitude t in [0,1].
vec3 palette(float t) {
    vec3 c = STOPS[0].yzw;
    for (int i = 0; i < NSTOPS - 1; i++)
        c = mix(c, STOPS[i + 1].yzw, smoothstep(STOPS[i].x, STOPS[i + 1].x, t));
    return c;
}

// Google's "Turbo" colormap — the rainbow every SDR waterfall wears, but
// perceptually even.  Compact polynomial fit (Anton Mikhailov, Apache-2.0).
vec3 turbo(float x) {
    x = clamp(x, 0.0, 1.0);
    const vec4 kR4 = vec4(0.13572138, 4.61539260, -42.66032258, 132.13108234);
    const vec4 kG4 = vec4(0.09140261, 2.19418839,   4.84296658, -14.18503333);
    const vec4 kB4 = vec4(0.10667330, 12.64194608, -60.58204836, 110.36276771);
    const vec2 kR2 = vec2(-152.94239396, 59.28637943);
    const vec2 kG2 = vec2(4.27729857, 2.82956604);
    const vec2 kB2 = vec2(-89.90310912, 27.34824973);
    vec4 v4 = vec4(1.0, x, x * x, x * x * x);
    vec2 v2 = v4.zw * v4.z;
    return clamp(vec3(dot(v4, kR4) + dot(v2, kR2),
                      dot(v4, kG4) + dot(v2, kG2),
                      dot(v4, kB4) + dot(v2, kB2)), 0.0, 1.0);
}

void mainImage(out vec4 O, in vec2 I) {
    vec2 uv = I / iResolution.xy;

    float m = texture(iChannel0, uv).x;             // raw 0..1 waterfall magnitude

    // Remap [FLOOR, CEIL] -> [0, 1], shape with GAMMA, then color.
    float t = clamp((m - FLOOR) / (CEIL - FLOOR), 0.0, 1.0);
    t = pow(t, GAMMA);
#if USE_TURBO
    vec3 col = turbo(t);
#else
    vec3 col = palette(t);
#endif
    // Soft floor gate: fade the lowest sliver to black so quiet zones go dark
    // without a hard edge.  Anything at/below FLOOR ends up black.
    col *= smoothstep(FLOOR, FLOOR + SOFT, m);

#if SHOW_GRID
    // Eight-division frequency grid: a faint cold tint between bins, brighter
    // at the band center.  GRID_LEVEL keeps it from re-lighting the dark floor.
    float g = abs(fract(uv.x * 8.0 - 0.5) - 0.5) / fwidth(uv.x * 8.0);
    col += vec3(0.5, 0.8, 1.0) * GRID_LEVEL * (1.0 - clamp(g, 0.0, 1.0));
    if (abs(uv.x - 0.5) < fwidth(uv.x)) col += vec3(0.0, 1.0, 1.6) * GRID_LEVEL;
#endif

    col *= BRIGHTNESS;                              // master level

#if SHOW_TRACE
    // Live spectrum drawn as a thin green trace hugging the top edge, so the
    // current sweep reads as a line above the history it's about to become.
    float s = texture(iChannel1, vec2(uv.x, 0.75)).x;       // 0..1 magnitude
    float ty = 1.0 - s * 0.18;                              // top ~18% of panel
    float line = smoothstep(2.5 / iResolution.y, 0.0, abs(uv.y - ty));
    col = mix(col, vec3(0.5, 1.0, 0.6), line * step(0.82, uv.y));
#endif

    O = vec4(col, 1.0);
}
