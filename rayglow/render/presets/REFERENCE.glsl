// ########## IMPORT THE MILK CHANNEL #######################################
// iChannel0: milk
// ########## IMPORT THE MILK CHANNEL #######################################
// The milk texture is 16x3 (feed v3): row 0 = band levels, row 1 = motion,
// row 2 = globals.  Full map: rayglow/render/textures.py (MilkChannel);
// live cards: milk-verbose (bands), milk-features (globals), milk-spectrum.
//
// Bands, cols 0-7 (log-spaced): 20-60 | 60-120 | 120-250 | 250-500 |
// 500-1k | 1k-2.5k | 2.5k-6k | 6k-16k Hz.  Col 8 = overall volume.
// 1.0 = "typical for this song right now"; hits spike 2-3.
    vec4 b0    = texelFetch(iChannel0, ivec2(0, 0), 0);  // .x imm  .y env0 ~125ms
    vec4 b2    = texelFetch(iChannel0, ivec2(2, 0), 0);  // .z env1 punchy flywheel
    vec4 b7    = texelFetch(iChannel0, ivec2(7, 0), 0);  // .w env2 heavy flywheel
    vec4 vol   = texelFetch(iChannel0, ivec2(8, 0), 0);
// Motion, same cols on row 1.  Thetas = "music time" (iTime that follows
// the band; ~1 rad/s typical, wraps at 200*pi — sin(theta*k) seamless for
// k a multiple of 0.01).  theta1/theta2 carry flywheel momentum.
    vec4 m0    = texelFetch(iChannel0, ivec2(0, 1), 0);  // .x th0 .y th1 .z th2 .w onset
// Globals, row 2.  beat_phase/bar_phase are PREDICTIVE ramps hitting 1.0
// ON the beat.  key: fract(.z)*12 = pitch class C..B, .z >= 1.0 = minor.
    vec4 tempo = texelFetch(iChannel0, ivec2(0, 2), 0);  // bpm/240 beat_ph bar_ph conf
    vec4 pulse = texelFetch(iChannel0, ivec2(1, 2), 0);  // beat downbeat key/12 key_conf
    vec4 desc  = texelFetch(iChannel0, ivec2(2, 2), 0);  // centroid flux flatness rolloff
    vec4 dyn   = texelFetch(iChannel0, ivec2(3, 2), 0);  // crest width pan -
    vec4 meta  = texelFetch(iChannel0, ivec2(7, 2), 0);  // pkt_age live source -
// Chroma: (4..6, 2), 4 pitch classes per texel, C..B.
// Legacy MilkDrop scalars (pre-v3 ports): (9,2) bass mid treb vol,
// (10,2) bass_att mid_att treb_att sub, (11,2).x sub_att.
// ########## MISC ##########################################################

// ##### ROTATE
mat2 rot(float a) { float s = sin(a), c = cos(a); return mat2(c, -s, s, c); }

// ##### COLOR PALETTE TOOL
vec3 palette(float t)
{
    vec3 a = vec3(0.5, 0.5, 0.5);
    vec3 b = vec3(0.5, 0.5, 0.5);
    vec3 c = vec3(1.0, 1.0, 1.0);
    vec3 d = vec3(0.0, 0.33, 0.67);
    return a + b * cos(6.28318 * (c * t + d));
}

// ##### A GODDAMN BOX
float sdBox(vec2 p, vec2 b)
{
    vec2 d = abs(p) - b;
    return length(max(d, 0.0)) + min(max(d.x, d.y), 0.0);
}
