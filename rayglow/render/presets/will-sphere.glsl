// will-sphere.glsl — palette rings radiating on the sub phase ("music time").
//
// (This header used to carry a stale copy of the old milk-verbose 8x1 texel
//  notes; the milk texture is now 16x3, feed v3 — see textures.py or
//  REFERENCE.glsl for the map.  Only meta.x — the sub theta — drives this.)
//
// iChannel0: milk

#define SCALE     1.0     // band bars: full height = this many "typicals"
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


// THE COLOR PALETTE
vec3 palette( float t) {
    vec3 a = vec3(0.3, 0.3, 0.5);
    vec3 b = vec3(-0.3, 0.3, 0.3);
    vec3 c = vec3(1.0, 1.0, 1.0);
    vec3 d = vec3(1.8, -1.0, 0.9);
    return a + b*cos( 6.28318*(c*t+d) );
}


//THE MAIN FUNCTION
void mainImage(out vec4 O, in vec2 I) {
    // Legacy scalars rebuilt on the v3 16x3 milk texture (see textures.py).
    // Old 13x1 texels 0-4 were (.x imm .y att .z ddt .w env): imm/att now live
    // in the legacy block (9..11,2); env = env0 (row 0 .y) of the nearest v3
    // band; ddt is gone in v3 (row 1 .w onset is its + half) -> 0.
    vec4 lvl   = texelFetch(iChannel0, ivec2(9, 2), 0);   // bass mid treb vol imm
    vec4 attv  = texelFetch(iChannel0, ivec2(10, 2), 0);  // atts + sub imm
    vec4 bass  = vec4(lvl.x, attv.x, 0.0, texelFetch(iChannel0, ivec2(2, 0), 0).y);
    vec4 mid   = vec4(lvl.y, attv.y, 0.0, texelFetch(iChannel0, ivec2(5, 0), 0).y);
    vec4 treb  = vec4(lvl.z, attv.z, 0.0, texelFetch(iChannel0, ivec2(7, 0), 0).y);
    vec4 vol   = vec4(lvl.w, lvl.w, 0.0, texelFetch(iChannel0, ivec2(8, 0), 0).y);
    vec4 sub   = vec4(attv.w, texelFetch(iChannel0, ivec2(11, 2), 0).x, 0.0,
                      texelFetch(iChannel0, ivec2(0, 0), 0).y);
    // old texel 5 (bass/mid/treb/vol theta) -> theta0 of the nearest v3 band
    vec4 theta = vec4(texelFetch(iChannel0, ivec2(2, 1), 0).x,
                      texelFetch(iChannel0, ivec2(5, 1), 0).x,
                      texelFetch(iChannel0, ivec2(7, 1), 0).x,
                      texelFetch(iChannel0, ivec2(8, 1), 0).x);
    // old texel 6 (.x sub theta, .yzw pkt_age/live/source_domain)
    vec4 meta  = vec4(texelFetch(iChannel0, ivec2(0, 1), 0).x,
                      texelFetch(iChannel0, ivec2(7, 2), 0).xyz);
    vec2 uv = (I * 8. / PANEL.xy) / PANEL.y;

    uv.x *= PANEL.x / PANEL.y;
    // uv.x = uv.x - 2.0;
    // uv = fract(uv);
    uv -= 4.0;
    uv.y += 3.5;
    float d = length(uv);

    vec3 col = palette(d + iTime);

    d = cos(d * 2. - meta.x * 4.) / 8.;
    d = abs(d);
    d = smoothstep(0.0, 0.1, d);
    d = 0.3 / d;
    col *= d;
    O = vec4(col, 1.0);
}
