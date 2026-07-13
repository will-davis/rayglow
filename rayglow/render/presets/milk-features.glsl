// milk-features.glsl — the feed's global scalars (milk row 2), one bar each.
//
// PURPOSE: a reference card, not a visualizer (sibling of milk-verbose.glsl,
// which covers the band rows).  When you want "the onset strength" or "is
// this a downbeat", find the bar here that moves the way you want, then copy
// the one texelFetch + the one use of the value.
//
// TEXEL MAP (milk row 2; rows 0-1 are on milk-verbose.glsl)
//   (0,2) tempo:   .x bpm/240  .y beat_phase  .z bar_phase  .w beat_conf
//                  beat_phase and bar_phase are PREDICTIVE ramps: they hit
//                  1.0 ON the (predicted) beat / bar, then wrap to 0 — so
//                  effects can anticipate a hit instead of reacting late.
//   (1,2) pulses:  .x beat (1 for one frame)  .y downbeat (every 4th)
//                  .z key_idx/12  .w key_conf
//                  key: fract(.z)*12 = pitch class (0 C .. 11 B); .z >= 1.0
//                  means minor.  Gate on .w — it's a mood, not ground truth.
//   (2,2) spectral: .x centroid  .y flux  .z flatness  .w rolloff
//   (3,2) dynamics: .x crest  .y width  .z pan
//   (7,2) meta:     .x pkt_age  .y live  .z source_domain
//
// SCREEN LAYOUT (columns; bottom-up bars)
//   descriptors  centroid 16  flux 28  flatness 40  rolloff 52  crest 64
//   tempo        bpm 90  beat_phase 100  bar_phase 110  beat_conf 120
//   key          cells 136-159 (2px per pitch class; green major / violet
//                minor, brightness = confidence; C and A faintly marked)
//   stereo       width 176  pan 200   (SIGNED: up=+ / down=- from mid line)
//   beat: full-frame border flashes on a beat (white) / downbeat (amber)
//   bottom-left dot: live (green real / red synth); hue tags source_domain
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
    if (px.y == floor(PANEL.y * 0.5) && px.x >= 168.0) O.rgb = vec3(0.10);

    vec4 tempo = texelFetch(iChannel0, ivec2(0, 2), 0); // bpm/240 phase bar conf
    vec4 pulse = texelFetch(iChannel0, ivec2(1, 2), 0); // beat downbeat key conf
    vec4 desc  = texelFetch(iChannel0, ivec2(2, 2), 0); // centroid flux flat roll
    vec4 dyn   = texelFetch(iChannel0, ivec2(3, 2), 0); // crest width pan -
    vec4 meta  = texelFetch(iChannel0, ivec2(7, 2), 0); // pkt_age live source -

    // ---- descriptors ((2,2) + crest from (3,2).x) ------------------------
    if (bar(px, 16.0, desc.x))         O.rgb = DESC;          // centroid 0..1
    if (bar(px, 28.0, desc.y / 3.0))   O.rgb = DESC;          // flux (1.0=typ)
    if (bar(px, 40.0, desc.z))         O.rgb = DESC;          // flatness 0..1
    if (bar(px, 52.0, desc.w))         O.rgb = DESC;          // rolloff 0..1
    if (bar(px, 64.0, dyn.x / 32.0))   O.rgb = DESC;          // crest (~peaky)

    // ---- tempo ((0,2)) ----------------------------------------------------
    // beat_phase ramps INTO the beat: 1.0 - tempo.y = "time until the hit".
    if (bar(px,  90.0, tempo.x))       O.rgb = TEMPO;         // bpm/240
    if (bar(px, 100.0, tempo.y))       O.rgb = TEMPO;         // beat_phase
    if (bar(px, 110.0, tempo.z))       O.rgb = TEMPO;         // bar_phase (4 beats)
    if (bar(px, 120.0, tempo.w))       O.rgb = TEMPO;         // beat_conf 0..1

    // ---- key ((1,2).zw): 12 cells, lit = detected pitch class ------------
    // key_idx/12 decodes as: fract()*12 = pitch class, >= 1.0 = minor.
    if (px.x >= 136.0 && px.x < 160.0 && px.y < 8.0) {
        int cell = int((px.x - 136.0) / 2.0);
        int pc = int(fract(pulse.z) * 12.0 + 0.5) % 12;
        bool minor = pulse.z >= 1.0;
        // faint C / A markers to orient the eye (like a keyboard's home keys)
        O.rgb = (cell == 0) ? vec3(0.0, 0.06, 0.0)
              : (cell == 9) ? vec3(0.06, 0.0, 0.0) : vec3(0.02);
        if (cell == pc)
            O.rgb = (minor ? vec3(0.6, 0.2, 1.0) : vec3(0.2, 1.0, 0.4))
                    * max(pulse.w, 0.15);                     // dim when unsure
    }

    // ---- stereo ((3,2).yz), SIGNED ----------------------------------------
    // width: +1 mono (green up), -1 anti-phase (red down).
    if (sbar(px, 176.0, dyn.y))  O.rgb = dyn.y >= 0.0 ? POS : NEG;
    // pan: +1 right (green up), -1 left (red down).
    if (sbar(px, 200.0, dyn.z))  O.rgb = dyn.z >= 0.0 ? POS : NEG;

    // ---- beat flash: a 1-px border that lights on the beat ----------------
    // pulse.x fires for one frame on each beat; pulse.y on every 4th
    // (downbeat).  With the predictive tracker these land ON the hit —
    // watch the border against the music, not after it.
    bool border = px.x == 0.0 || px.x == PANEL.x - 1.0
               || px.y == 0.0 || px.y == PANEL.y - 1.0;
    if (border && pulse.y > 0.5)      O.rgb = vec3(1.0, 0.7, 0.1);   // downbeat
    else if (border && pulse.x > 0.5) O.rgb = vec3(1.0);             // beat

    // ---- live + source dot (bottom-left) ----------------------------------
    // green = real packets, red = synth fallback; nudged toward blue as the
    // source_domain rises (0 audio, 1 sdr, 2 telemetry, ...).
    if (px.x < 2.0 && px.y < 2.0) {
        vec3 c = mix(vec3(0.8, 0.1, 0.1), vec3(0.1, 0.9, 0.2), meta.y);
        O.rgb = mix(c, vec3(0.2, 0.4, 1.0), clamp(meta.z / 4.0, 0.0, 1.0));
    }
}
