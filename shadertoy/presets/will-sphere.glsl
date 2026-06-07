// milk-verbose.glsl — every float in the milk texture as its own 1-px bar.
//
// PURPOSE: a reference card, not a visualizer.  When you're building
// something and want "the sub envelope" or "is the feed live", find the
// bar here that moves the way you want, then copy the two lines of code
// that drew it (one texelFetch + one use of the value).
//
// HOW THE MILK TEXTURE WORKS
//   The pipeline uploads an 8x1 texture: 8 texels in a row, each holding
//   4 floats.  ".x .y .z .w" are just names for the 4 slots (".rgba" are
//   the SAME slots — two naming schemes, one vec4).  texelFetch reads one
//   exact texel, no filtering, no normalized coords:
//
//       vec4 bass = texelFetch(iChannel0, ivec2(0, 0), 0);
//                                         ^col  ^row   ^mip level (always 0)
//
//   After that, bass.x is a plain float you can do math with.
//
// TEXEL MAP (what the pipeline puts in each slot)
//   texels 0 bass, 1 mid, 2 treb, 3 vol, 4 sub — per band:
//     .x imm   instant level, 1.0 = "typical for this song" (hits: 2-4)
//     .y att   sender-smoothed level (slow swells)
//     .z ddt   d/dt of imm, SIGNED: + while rising, - while falling
//     .w env   imm through a ~125ms lag (ready-made amplitude control)
//   texel 5: integrated phase ("music time") .x bass .y mid .z treb .w vol
//   texel 6: .x sub phase   .y packet age, seconds   .z live (1 UDP, 0 synth)
//
// SCREEN LAYOUT (panel columns; band colors match milkfeed.glsl)
//   bass 8-17   mid 32-41   treb 56-65   vol 80-89   sub 104-113
//     within each group: +0 imm (band color)      +3 att (white)
//                        +6 ddt (cyan up/red dn)  +9 env (amber)
//   140-152  theta per band, drawn as fract(theta/2pi): a sawtooth that
//            climbs faster when that band is loud
//   170 packet age (full bar = >=1s silence)   176 live flag (all/nothing)
//   dim row 0 strips = group labels; dim mid line = the 1.0 "typical" level
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
    vec4 bass  = texelFetch(iChannel0, ivec2(0, 0), 0);
    vec4 mid   = texelFetch(iChannel0, ivec2(1, 0), 0);
    vec4 treb  = texelFetch(iChannel0, ivec2(2, 0), 0);
    vec4 vol   = texelFetch(iChannel0, ivec2(3, 0), 0);
    vec4 sub   = texelFetch(iChannel0, ivec2(4, 0), 0);
    vec4 theta = texelFetch(iChannel0, ivec2(5, 0), 0);
    vec4 meta  = texelFetch(iChannel0, ivec2(6, 0), 0);
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
