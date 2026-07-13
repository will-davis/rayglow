// milk-spectrum.glsl — the v2 feed's real spectrum + chroma, as a reference.
//
// PURPOSE: show the two big v2 arrays the old wire never carried — the
// 512-bin log spectrum and the 12-bin chroma — so you can see their shape and
// copy the two lines that sample them.
//
// THE SPECTRUM CHANNEL ('spectrum'): a 512x1 float texture, value 0..1
// (dB-normalized).  Its x axis is a HYBRID linear+log scale (mirrors the
// sender): the first ~162 slots are LINEAR at one FFT bin each (30 Hz up to
// ~1.9 kHz), the remaining ~350 are LOG up to 16 kHz.  This packs every slot
// with real FFT data — no interpolated holes in the low end.  Sample it like a
// 1-D LUT — linear filtered, so a normalized x interpolates between bins:
//       float m = texture(iChannel1, vec2(px.x / PANEL.x, 0.5)).x;
// (Unlike the 'audio' texture's spectrum row, this is the real dedicated FFT,
// full float range — not the clamped waveform round-trip.  If you want a clean
// all-log display, remap x here in the shader — the wire stays max-entropy.)
//
// CHROMA lives in the milk texture, texels 10-12 (.xyzw each), pitch classes
// C C# D D# | E F F# G | G# A A# B, peak-normalized 0..1.  GLES3 lets us index
// a vec4 by a computed component, so 12 cells map cleanly.
//
// SCREEN LAYOUT
//   rows 10..63  spectrum fill (height + color = magnitude), low freq at left
//   rows 0..8    12 chroma cells (brightness = pitch-class energy)
//   dim vertical line = spectral centroid (milk texel 7.x), placed on the
//                       hybrid lin/log axis so it lines up with the spectrum
//   top-left dot = live (green real / red synth)
//
// iChannel0: milk
// iChannel1: spectrum

const vec2 PANEL = vec2(256.0, 64.0);
const float FLOOR_Y = 10.0;             // spectrum sits above the chroma strip

// A compact blue->cyan->green->yellow->red ramp for magnitude (same family as
// radio-waterfall.glsl) — low energy cool, high energy hot.
vec3 ramp(float t) {
    t = clamp(t, 0.0, 1.0);
    vec3 c = mix(vec3(0.0, 0.0, 0.25), vec3(0.0, 0.6, 1.0), smoothstep(0.0, 0.35, t));
    c = mix(c, vec3(0.1, 1.0, 0.4), smoothstep(0.35, 0.6, t));
    c = mix(c, vec3(1.0, 0.9, 0.1), smoothstep(0.6, 0.82, t));
    c = mix(c, vec3(1.0, 0.2, 0.15), smoothstep(0.82, 1.0, t));
    return c;
}

void mainImage(out vec4 O, in vec2 I) {
    vec2 px = floor(I / iResolution.xy * PANEL);
    O = vec4(0.0, 0.0, 0.0, 1.0);

    // ---- spectrum fill (rows FLOOR_Y..top) ------------------------------
    float m = texture(iChannel1, vec2((px.x + 0.5) / PANEL.x, 0.5)).x;
    float top = FLOOR_Y + m * (PANEL.y - FLOOR_Y);
    if (px.y >= FLOOR_Y && px.y < top) {
        // brighten the leading pixel so the envelope reads as a line too.
        float lead = (top - px.y < 1.0) ? 1.0 : 0.7;
        O.rgb = ramp(m) * lead;
    }

    // ---- spectral centroid marker ---------------------------------------
    // centroid (texel 7.x) is 0..1 of Nyquist (linear Hz).  Convert Hz -> slot
    // by inverting the sender's HYBRID axis: linear below SPEC_FC, log above.
    // The four constants below mirror sender.py (SPEC_FMIN/FMAX, SPEC_NLIN,
    // _SPEC_FC) — keep them in sync if the sender's axis params change.
    const float SPEC_FMIN = 30.0, SPEC_FMAX = 16000.0;
    const float SPEC_OUT = 512.0, SPEC_NLIN = 162.0, SPEC_FC = 1928.4;
    float dF = (SPEC_FC - SPEC_FMIN) / SPEC_NLIN;                 // FFT bin width
    float R  = pow(SPEC_FMAX / SPEC_FC, 1.0 / (SPEC_OUT - SPEC_NLIN));
    float cenHz = max(texelFetch(iChannel0, ivec2(7, 0), 0).x * (iSampleRate * 0.5),
                      SPEC_FMIN);
    float idx = (cenHz <= SPEC_FC) ? (cenHz - SPEC_FMIN) / dF
                                   : SPEC_NLIN + log2(cenHz / SPEC_FC) / log2(R);
    float cx = floor(idx / SPEC_OUT * PANEL.x);
    if (px.x == cx && px.y >= FLOOR_Y) O.rgb = max(O.rgb, vec3(0.5, 0.5, 0.5));

    // ---- chroma strip (rows 0..8) ---------------------------------------
    if (px.y < 9.0) {
        int pc = int(px.x * 12.0 / PANEL.x);          // which pitch class
        int texel = 10 + pc / 4;                       // texels 10,11,12
        int comp = pc - (pc / 4) * 4;                  // component 0..3
        float v = texelFetch(iChannel0, ivec2(texel, 0), 0)[comp];
        // 1-px gaps between cells so the 12 are countable.
        bool gap = (int(px.x * 12.0 / PANEL.x) != int((px.x + 1.0) * 12.0 / PANEL.x));
        // root note C and the octave A get a faint tint to orient the eye.
        vec3 base = (pc == 0) ? vec3(0.0, 0.15, 0.0)
                  : (pc == 9) ? vec3(0.15, 0.0, 0.0) : vec3(0.05);
        O.rgb = gap ? vec3(0.0) : base + vec3(0.2, 0.9, 1.0) * v;
    }

    // ---- live dot (top-left) --------------------------------------------
    if (px.x < 2.0 && px.y > PANEL.y - 3.0) {
        float live = texelFetch(iChannel0, ivec2(6, 0), 0).z;
        O.rgb = mix(vec3(0.8, 0.1, 0.1), vec3(0.1, 0.9, 0.2), live);
    }
}
