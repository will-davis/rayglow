// milkfeed.glsl — raw diagnostic view of the milk feed.  No buffer file,
// no dt, no smoothing: every frame draws exactly what arrived.
//
// Bottom ~2/3: bar graph of the five auto-gained legacy band scalars
//   sub = magenta   bass = red   mid = green   treb = blue   vol = white
//   (sub = true 23-117Hz subwoofer band, protocol v1.  "bass" is MilkDrop's
//    band = 0-4kHz low-mids — that's why it never tracked your subwoofer.)
//   - filled bar           = imm  (instant, jumps per frame)
//   - bright white tick    = att  (the sender's smoothed version)
//   - amber tick           = env  (env0, ~125ms, of the nearest v3 band)
//   - dim line across all  = 1.0 "typical level" reference; bars above it
//     mean louder-than-usual, below it quieter.  Full height = SCALE typicals.
//   On the v3 16x3 milk texture (see textures.py) imm/att live in the legacy
//   block — (9,2) bass/mid/treb/vol, (10,2) atts + sub imm, (11,2).x sub_att —
//   and env0 is row 0 .y of the nearest v3 band (b2/b5/b7/vol/b0).  The old
//   per-band d/dt is gone; row 1 .w (onset) is its useful positive half.
//
// Top ~1/3: the raw 128-sample waveform (audio texture row y=0.75),
//   0.5 = silence centerline.  Top-left corner dot: green = real packets,
//   red = synth fallback (milk (7,2).y).
//
// iChannel0: milk
// iChannel1: audio

#define SCALE 2.0      // bar full-height = this many "typicals"

// Legacy band i (0 bass, 1 mid, 2 treb, 3 vol, 4 sub) rebuilt in the old
// 13x1 texel shape: .x imm  .y att  .z 0 (was d/dt)  .w env.
vec4 legacyBand(int i) {
    vec4 lvl = texelFetch(iChannel0, ivec2(9, 2), 0);    // bass mid treb vol imm
    vec4 att = texelFetch(iChannel0, ivec2(10, 2), 0);   // atts + sub imm
    float imm = i == 0 ? lvl.x : i == 1 ? lvl.y : i == 2 ? lvl.z
              : i == 3 ? lvl.w : att.w;
    float smo = i == 0 ? att.x : i == 1 ? att.y : i == 2 ? att.z
              : i == 3 ? lvl.w                         // vol att = vol imm (as before)
              : texelFetch(iChannel0, ivec2(11, 2), 0).x;
    int ec = i == 0 ? 2 : i == 1 ? 5 : i == 2 ? 7 : i == 3 ? 8 : 0;
    float env = texelFetch(iChannel0, ivec2(ec, 0), 0).y;   // env0 ~125ms
    return vec4(imm, smo, 0.0, env);
}

void mainImage(out vec4 O, in vec2 I) {
    vec2 uv = I / iResolution.xy;
    O = vec4(0.0, 0.0, 0.0, 1.0);

    if (uv.y < 0.62) {                          // ---- band bars ----
        float fy = uv.y / 0.62;                 // 0..1 inside the bar region
        int band = int(uv.x * 5.0);
        float fx = fract(uv.x * 5.0);
        if (fx < 0.05 || fx > 0.95) return;     // gaps between bars

        // display order: sub, bass, mid, treb, vol -> legacy band index
        int texel = band == 0 ? 4 : band - 1;
        vec4 s = legacyBand(texel);
        vec3 col = band == 0 ? vec3(1.0, 0.2, 1.0)
                 : band == 1 ? vec3(1.0, 0.25, 0.2)
                 : band == 2 ? vec3(0.2, 1.0, 0.3)
                 : band == 3 ? vec3(0.25, 0.4, 1.0)
                 :             vec3(0.9);

        if (fy < s.x / SCALE)               O.rgb = col * 0.8;   // imm fill
        if (abs(fy - s.w / SCALE) < 0.05)                        // env tick
            O.rgb = vec3(1.0, 0.7, 0.1);
        if (abs(fy - s.y / SCALE) < 0.05)   O.rgb = vec3(1.0);   // att tick
        if (abs(fy - 1.0 / SCALE) < 0.025)                       // 1.0 ref
            O.rgb = max(O.rgb, vec3(0.28));
    } else {                                    // ---- waveform ----
        float fy = (uv.y - 0.62) / 0.38;
        float w = texture(iChannel1, vec2(uv.x, 0.75)).x;
        O.rgb = vec3(0.3, 1.0, 0.8) * smoothstep(0.10, 0.02, abs(fy - w));
        if (I.x < 3.0 && I.y > iResolution.y - 4.0) {            // live dot
            float live = texelFetch(iChannel0, ivec2(7, 2), 0).y;
            O.rgb = mix(vec3(0.8, 0.1, 0.1), vec3(0.1, 0.9, 0.2), live);
        }
    }
}
