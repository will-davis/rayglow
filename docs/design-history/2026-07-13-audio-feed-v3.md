# Audio feed v3 — the packet overhaul (shipped 2026-07-13)

The design record for feed v3: Will's brief (verbatim, from the ROADMAP fill-in
block), the v2 layout it replaced, and the decisions taken. Current truth lives in
`sender/README.md` (packet + features), `rayglow/render/textures.py` (texel maps),
and `rayglow/feed/receiver.py` (all accepted versions).

## Will's brief (verbatim)

> Will: HI! The original Milkdrop port included a handful of FFT bins that
> only targeted mid-range frequencies, mid-bass to mid-treble. We increased
> that range by adding a dedicated "sub" frequency which by and large is the
> the most useful visual-audio variable to generate movement/effects with.
> Eventually while trying to animate difference visualizers
> I became stuck on an issue of trying to change the "rate" of iTime in
> a single pass parallel shader pipe-line. Either I add a buffer so I
> can calculate d/dt or I have the backend do it for me. Considering that
> backend is my over-powered will-desktop, I had no issues doing the FFT
> and calculations over here to allow the usually gpu/cpu bound rpi5
> to have the processed information fed to it, ready to go.
> This worked *very* well. The specific data I have found absolutely
> key to fluid, punchy, visually appealing audio-visualizer shaders
> are the `.w env   imm through a ~125ms lag (ready-made amplitude control)`
> envelope variables calculated from ~125ms for all the chosen bands, (even vol)
> Moreso, the theta/meta.x
> variables that mimic iTime but instead step forward when their bin is high
> amplitutde, which I can turn into a looping phase and give moving objects acceleration
> instead of instant, spiky changes. These are all reference in:
> `rayglow/render/presets/milk-verbose.glsl`
>
>    vec4 theta = texelFetch(iChannel0, ivec2(5, 0), 0);
>    vec4 meta  = texelFetch(iChannel0, ivec2(6, 0), 0);
>
> The v2 approach I went into with the idea of just getting more interesting information
> over to the rpi, without thinking of how worthwhile that information was. I want to consider
> splitting the existing sub - bass - mid - treb into more bins, but not nearly as many as
> the updated v2 created. I would also like to expand the "flywheel" envelope values,
> creating up to three envelope readings, each with more "momentum" than the previous,
> with the current 125ms being the minimum. I would like a stronger "flywheel" that I
> can use without having to resort to buffers. If a bass punches hard, I would like that variables to
> to have different decay rates. If I use this for something like a spinning object, it quite literally
> visually functions represetns a flywheel. That "momentum" is what I'm struggling to create.
> also - the d/dt value isn't really needed. It is fine to computer on the client side for use calcs,
> but I find a signal that changes pos-neg so quickly is mostly useless. It's just jarring and
> and not pleasant to look at unless I surpress it to the point its effect doesn't appear.
>
> I would like to keep the instantaneous values for all, as I do make use of those. So
> essentially ~8 FFT bands, very low sub to very high treb, each with three flywheel envelopes, and
> each with three associated theta/meta type values that increase (and loop eventually) to use as iTime
> replacements. I would like to keep the new, similar items added in the v2 rollout, seen in
> ... presets/milk-features.glsl. The flux, the pan, swell, all interest me. However, the beat
> detection drove me nuts. Please look into refining that. I could not make sense of it and it rarely
> appeared to correlate with the beat of any music I had playing. If there is a better way
> to implement that, I would love to have that feature.
>
> As for the spectrum data. I vote we go to 1x128 or 1x256. Regardless, the screen is just
> not a high enough resolution to make much use out of such a dense band of information.
> (I may be wrong here, just I have no personally found much use). If I want a per pixel
> value across the x-axis, I can just mirror the think or fudge it.
>
> A few other ideas: I would potentially like to to be able to pass some form of
> smooth wave function, akin to fitting a polynomial on the the 8 (or however many) main
> FFT bins. The v2 had so many bands, isolating them was worthless, the band was so narrow
> that the audio they tracked didn't really sync in a noticeable way with the music. while
> we gained more "information" it was really just a lot of really accurate noise.
>
> And finally - when moving the v2 an effort was made to preserve the original Milk bands and
> and texel locations. This time around they can be discarded. As long as some of the new values
> are rough approximations (even if I have to do a (deepsub.w * 1.5 + lowsub * 0.5) = the class sub.w
> that is fine. I can quickly comb through my shaders and replace the values to get the original
> functionality back.
>
> As always - I am 100% open to suggestions. If there is a cool tool, method, approach, that I have
> not considered, that would be worthwhile to add, I would like to try it. Please take some time to
> consider this. What is out there, what is used, what would fit in with what we're doing.
> Thanks for coming to my ~TED Talk~ ramble.

## Decisions (with Will, 2026-07-13)

- **8 log-spaced bands** 20 Hz–16 kHz (20|60|120|250|500|1k|2.5k|6k|16k), multi-
  resolution: b0–b3 from the existing 4096-pt FFT, b4–b7 from the snappy 576-window
  FFT; no equalize — per-band AutoGain is the leveling.
- **Flywheel ballistics: punchy asymmetric** (Will's pick over symmetric inertia or a
  hybrid): tier0 = classic symmetric ~125 ms; tier1 ~60 ms attack / ~500 ms decay;
  tier2 ~150 ms / ~2 s. `ENV_TIERS` in sender.py is the tuning table.
- **Thetas integrate (imm, env1, env2)** — theta0 keeps today's beloved feel exactly;
  theta1/theta2 give rotation *momentum*. All wrap at 200π. Integration moved
  **sender-side** onto its steady clock (was Pi-side in `MilkChannel`, against jittery
  packet arrival — the v2 `dt` came from packet `t` deltas).
- **d/dt dropped** (jarring); its useful half survives as per-band one-sided **onset**
  (half-wave-rectified flux, own AutoGains) — Will opted in.
- **Beat tracker rebuilt** as a clean-room DAFx-09 implementation (`sender/beat.py`):
  comb-filterbank tempo induction (Rayleigh + continuity priors), cumulative-score
  alignment, slew-limited PLL beat grid, **predictive** beat_phase (ramps into the
  beat) + bar_phase. v2's flag-on-loud-onset + phase-halving heuristic is what "drove
  him nuts" — it reacted late and re-synced randomly. (BTrack is GPL: algorithm from
  the papers, no code ported.)
- **Spectrum 512 → 128** (Will's pick over 256): the wall is 256 px wide; v2's
  512 narrow bins were "really accurate noise". Axis constants recomputed
  (NLIN=23, FC=299.53125 Hz, R≈1.0386138; the sender prints them at startup).
- **Smooth wave function**: shipped as `.y`/`.z` rows of the (LINEAR-filtered)
  spectrum texture — Catmull-Rom-style curves through the 8 band values, computed
  Pi-side by one fixed (128×8) basis-matrix multiply. Kept off the wire so curve
  resolution isn't a protocol field.
- **Key detection** (Krumhansl-Schmuckler on a chroma EMA) — Will opted in.
- **Legacy bands kept on the wire** (header prefix unchanged, still computed by the
  untouched MilkDrop path) and surfaced in a legacy texel block (9–11, 2) — better
  than Will's offered "rough approximations": exact values, mechanical shader ports,
  and old senders (esp32-mic, raytop telemetry) stay visible.
- **milk texture 13×1 → 16×3** (levels / motion / globals rows, spares for the
  future); packet 4236 B → 2996 B.

## What v2 looked like (superseded)

Packet v2, 4236 B, `"<IHHIf7f2f512f512f12f5f3f2f"`: header (magic/ver/flags/seq/t),
7 legacy bands, sub/sub_att, wave[512], spec[512] (hybrid axis NLIN≈162,
FC≈1.9 kHz), chroma[12], centroid/flux/flatness/rolloff/crest,
bpm/beat_phase/beat_conf, width/pan. The receiver still accepts it (and v0/v1)
forever — see `rayglow/feed/receiver.py`.

milk texture 13×1 (superseded texel map):
texels 0–4 = bass/mid/treb/vol/sub with `.x` imm `.y` att `.z` ddt `.w` env
(ddt/env/theta were derived **on the Pi**, `MilkChannel` DDT_LAG=25/ENV_LAG=8);
texel 5 = thetas b/m/t/v; texel 6 = sub theta + pkt_age/live/source_domain;
texel 7 = descriptors; texel 8 = crest + bpm/240 + beat_phase + beat_conf;
texel 9 = beat/downbeat/width/pan; texels 10–12 = chroma. The old→new porting
table is in `sender/README.md`.

v2's beat detector (superseded): 6 s autocorrelation of AGC'd flux, argmax over
70–180 BPM integer frame lags (±2% BPM quantization at 60 Hz), phase advanced
open-loop with a "halve the phase on any onset > 1.8" re-sync heuristic.
