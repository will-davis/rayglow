// milkfeed.glsl — raw diagnostic view of the milk feed.  No buffer file,
// no dt, no smoothing: every frame draws exactly what arrived.
//
// Bottom ~2/3: bar graph of the five auto-gained band scalars
//   sub = magenta   bass = red   mid = green   treb = blue   vol = white
//   (sub = true 23-117Hz subwoofer band, protocol v1.  "bass" is MilkDrop's
//    band = 0-4kHz low-mids — that's why it never tracked your subwoofer.)
//   - filled bar           = .x  (imm: instant, jumps per frame)
//   - bright white tick    = .y  (att: the sender's smoothed version)
//   - amber tick           = .w  (env: Pi-side ~125ms envelope of imm)
//   - dim line across all  = 1.0 "typical level" reference; bars above it
//     mean louder-than-usual, below it quieter.  Full height = SCALE typicals.
//   (also available, not drawn: .z = d/dt of imm; texel 5 = integrated
//    phase theta per band; texel 6 = sub theta / packet age / live flag)
//
// Top ~1/3: the raw 128-sample waveform (audio texture row y=0.75),
//   0.5 = silence centerline.  Top-left corner dot: green = real packets,
//   red = synth fallback (milk texel 6 .z).
//
// iChannel0: milk
// iChannel1: audio

#define SCALE 2.0      // bar full-height = this many "typicals"

void mainImage(out vec4 O, in vec2 I) {
    vec2 uv = I / iResolution.xy;
    O = vec4(0.0, 0.0, 0.0, 1.0);

    if (uv.y < 0.62) {                          // ---- band bars ----
        float fy = uv.y / 0.62;                 // 0..1 inside the bar region
        int band = int(uv.x * 5.0);
        float fx = fract(uv.x * 5.0);
        if (fx < 0.05 || fx > 0.95) return;     // gaps between bars

        // display order: sub, bass, mid, treb, vol -> milk texel index
        int texel = band == 0 ? 4 : band - 1;
        vec4 s = texelFetch(iChannel0, ivec2(texel, 0), 0);
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
            float live = texelFetch(iChannel0, ivec2(6, 0), 0).z;
            O.rgb = mix(vec3(0.8, 0.1, 0.1), vec3(0.1, 0.9, 0.2), live);
        }
    }
}
