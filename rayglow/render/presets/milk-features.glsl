// milk-features.glsl — the v2 feed's scalar features, one labeled bar each.
//
// PURPOSE: a reference card, not a visualizer (sibling of milk-verbose.glsl,
// which covers the v1 bands/derived signals).  When you want "the onset
// strength" or "is this a downbeat", find the bar here that moves the way you
// want, then copy the one texelFetch + the one use of the value.
//
// All of these live in the milk texture (texels 7-9), so this card only needs
// iChannel0.  They are ZERO with a v0/v1 sender — run sender.py (v2) or
// `python -m rayglow.fake_sender` to drive them.
//
// TEXEL MAP (the new v2 slots; texels 0-6 are on milk-verbose.glsl)
//   texel 7 spectral:  .x centroid  .y flux  .z flatness  .w rolloff
//   texel 8 dynamics:  .x crest  .y bpm/240  .z beat_phase  .w beat_conf
//   texel 9 beat+stereo: .x beat  .y downbeat  .z width  .w pan
//
// SCREEN LAYOUT (columns; bottom-up bars)
//   descriptors  centroid 16  flux 28  flatness 40  rolloff 52  crest 64
//   tempo        bpm 96  beat_phase 108  beat_conf 120
//   stereo       width 152  pan 176   (SIGNED: bar grows up=+ / down=- from mid)
//   beat: full-frame border flashes on a beat (white) / downbeat (amber)
//   bottom-left dot: live (green real / red synth); its hue also tags source
//
// iChannel0: milk

const vec2 PANEL = vec2(256.0, 64.0);   // logical LED grid (full two-chain wall)

const vec3 DESC  = vec3(0.3, 0.8, 1.0);   // descriptor bars (cyan)
const vec3 TEMPO = vec3(1.0, 0.7, 0.1);   // tempo bars (amber)
const vec3 POS   = vec3(0.3, 1.0, 0.4);   // signed bar, positive (green)
const vec3 NEG   = vec3(1.0, 0.3, 0.3);   // signed bar, negative (red)

// True when panel pixel `px` is in a 1-px bar at `col` of height `value`
// (0..1 of the panel; y counts up from the bottom).
bool bar(vec2 px, float col, float value) {
    return px.x == col && px.y < clamp(value, 0.0, 1.0) * PANEL.y;
}

// Signed bar centered on the mid line: grows up for +, down for -.
bool sbar(vec2 px, float col, float value) {
    float mid = floor(PANEL.y * 0.5);
    float h = clamp(abs(value), 0.0, 1.0) * (PANEL.y * 0.5);
    return px.x == col && (value >= 0.0 ? (px.y >= mid && px.y < mid + h)
                                        : (px.y < mid && px.y >= mid - h));
}

void mainImage(out vec4 O, in vec2 I) {
    vec2 px = floor(I / iResolution.xy * PANEL);
    O = vec4(0.0, 0.0, 0.0, 1.0);

    // dim "full bar = 1.0" reference line for the unsigned bars, and a mid
    // line for the signed (stereo) ones.
    if (px.y == 0.0 && px.x < 132.0) O.rgb = vec3(0.08);
    if (px.y == floor(PANEL.y * 0.5) && px.x >= 144.0) O.rgb = vec3(0.10);

    vec4 spec = texelFetch(iChannel0, ivec2(7, 0), 0);  // centroid flux flat rolloff
    vec4 dyn  = texelFetch(iChannel0, ivec2(8, 0), 0);  // crest bpm/240 phase conf
    vec4 bs   = texelFetch(iChannel0, ivec2(9, 0), 0);  // beat downbeat width pan
    vec4 meta = texelFetch(iChannel0, ivec2(6, 0), 0);  // .z live, .w source_domain

    // ---- descriptors (texel 7 + crest from texel 8.x) -------------------
    if (bar(px, 16.0, spec.x))         O.rgb = DESC;          // centroid 0..1
    if (bar(px, 28.0, spec.y / 3.0))   O.rgb = DESC;          // flux (1.0=typ)
    if (bar(px, 40.0, spec.z))         O.rgb = DESC;          // flatness 0..1
    if (bar(px, 52.0, spec.w))         O.rgb = DESC;          // rolloff 0..1
    if (bar(px, 64.0, dyn.x / 32.0))   O.rgb = DESC;          // crest (~peaky)

    // ---- tempo (texel 8.yzw) --------------------------------------------
    if (bar(px,  96.0, dyn.y))         O.rgb = TEMPO;         // bpm/240
    if (bar(px, 108.0, dyn.z))         O.rgb = TEMPO;         // beat_phase 0..1
    if (bar(px, 120.0, dyn.w))         O.rgb = TEMPO;         // beat_conf 0..1

    // ---- stereo (texel 9.zw), SIGNED ------------------------------------
    // width: +1 mono (green up), -1 anti-phase (red down).
    if (sbar(px, 152.0, bs.z))  O.rgb = bs.z >= 0.0 ? POS : NEG;
    // pan: +1 right (green up), -1 left (red down).
    if (sbar(px, 176.0, bs.w))  O.rgb = bs.w >= 0.0 ? POS : NEG;

    // ---- beat flash: a 1-px border that lights on the beat --------------
    // bs.x pulses for one frame on each beat; bs.y on every 4th (downbeat).
    bool border = px.x == 0.0 || px.x == PANEL.x - 1.0
               || px.y == 0.0 || px.y == PANEL.y - 1.0;
    if (border && bs.y > 0.5)      O.rgb = vec3(1.0, 0.7, 0.1);   // downbeat
    else if (border && bs.x > 0.5) O.rgb = vec3(1.0);             // beat

    // ---- live + source dot (bottom-left) --------------------------------
    // green = real packets, red = synth fallback; nudged toward blue as the
    // source_domain rises (0 audio, 1 sdr, 2 telemetry, ...).
    if (px.x < 2.0 && px.y < 2.0) {
        vec3 c = mix(vec3(0.8, 0.1, 0.1), vec3(0.1, 0.9, 0.2), meta.z);
        O.rgb = mix(c, vec3(0.2, 0.4, 1.0), clamp(meta.w / 4.0, 0.0, 1.0));
    }
}
