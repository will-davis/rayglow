# sender — RayGLow desktop feature daemon

The broadcast half of RayGLow. `sender.py` (one file) captures whatever the desktop is
playing, reduces it to a handful of per-frame audio features, and unicasts them over UDP
at ~60 Hz to the Pi, which renders them as GLSL and drives a 256×64 HUB75 wall (via an
RP2350 over a 4-lane parallel PIO bus). For the system-level picture and the renderer,
see the [top-level README](../README.md).

This is a standalone uv project: it shares no code with the `rayglow` package — only the
**packet contract** (mirrored in `rayglow/feed/receiver.py`). The daemon only ever
*runs* on the desktop, since it's capturing desktop audio.

Two hardware senders live alongside it, for feeding the wall from a room microphone
instead of the desktop's audio: [`esp32-mic/`](esp32-mic/) (ESP32 + I2S mic firmware
that computes the same features on-chip) and [`espnow-dongle/`](espnow-dongle/) (an
ESP-NOW → UDP bridge for the Pi). Each has its own README.

## Running

```fish
cd sender
uv run sender.py                  # monitor of default sink -> 192.168.0.50:5005
uv run sender.py --list-sources   # enumerate pulse sources
uv run sender.py --source NAME    # capture a specific source instead
uv run sender.py --debug          # adds raw pre-normalization band energies
uv run sender.py --host H --port N --fps N
```

Prints a 1 Hz status line (the 8 band values, vol, bpm/conf, bar phase, key, centroid,
pan; `--debug` adds the legacy bands and raw energies) and a startup banner with the
packet size + spectrum-axis constants (mirror those into `milk-spectrum.glsl` if the
axis ever changes). Python ≥3.13,
numpy + sounddevice; `pactl` must be on PATH. Capture mechanics: the ALSA "pulse"
PortAudio device is a PipeWire/Pulse client, and the `PULSE_SOURCE` environment variable
selects which source it records — which is why `sender.py` defers `import sounddevice`
until *after* setting it.

## The features

Everything rides on **AutoGain**, ported from MilkDrop (`plugin.cpp:8750`): each band is
divided by its own long-running average. So a band value of **1.0 means "typical for
this song right now"** — quiet passages dip toward ~0.5, hits spike to 2–4 — regardless
of genre, mastering level, or system volume. This is why one shader works for
everything. Each band also has an `_att` twin (temporally smoothed: slow swells instead
of per-frame punch).

### The v3 bands + flywheels (the primary feed)

8 log-spaced bands, `b0`–`b7`, edges **20 | 60 | 120 | 250 | 500 | 1000 | 2500 | 6000 |
16000 Hz** (`BAND_EDGES_V3`, one tunable table). The low four read the 4096-pt spectrum
FFT (11.7 Hz/bin — real low-end resolution; b0's floor is effectively ~23 Hz, its first
usable bin), the high four the snappy 576-window/1024-pt FFT (12 ms). **No equalize on
either** — the per-band AutoGain *is* the leveling. Per band:

| field | what it is |
|---|---|
| `band_imm[8]` | instant AutoGained level (clamped ≤ 16 against AGC blow-ups on quiet bands) |
| `band_env[8][3]` | the **flywheel envelopes** (`ENV_TIERS`): tier0 = symmetric ~125 ms lag (the classic env feel); tier1 = punchy, ~60 ms attack / ~500 ms decay; tier2 = heavy, ~150 ms attack / ~2 s decay. A kick slams a tier up and it sails down — "momentum" |
| `band_theta[8][3]` | "music time" phase accumulators, wrap at 200π: theta0 integrates imm, theta1 env1, theta2 env2 — iTime replacements whose rotation *accelerates* with the music instead of stepping |
| `band_onset[8]` | per-band half-wave-rectified flux, own AutoGain — one-sided attack spikes (the useful half of the retired d/dt) |
| `vol_imm, vol_env[3], vol_theta[3]` | the same treatment for overall level |

All envelope/theta integration happens **here**, on the sender's steady wall clock —
the Pi just uploads texels (pre-v3 it integrated against jittery packet arrival times).

### Legacy layer (unchanged MilkDrop port, still shipped)

| field | what it is |
|---|---|
| `bass`, `mid`, `treb` | MilkDrop's three bands: 576-sample window → Hann → 1024-pt FFT → 512 bins × log-equalize → three equal *linear* thirds of the bottom half ≈ 0–4 / 4–8 / 8–12 kHz at 48 kHz |
| `vol` | sum of the three, own AutoGain |
| `wave[512]` | mono waveform window, resampled 576 → 512, ±1.0 |
| `sub`, `sub_att` | **v1, not MilkDrop**: true sub-bass. MilkDrop's "bass" covers 0–4 kHz with a log-equalize that suppresses the lowest bins ~90× — a subwoofer is invisible in it. `sub` uses a separate 2048-sample FFT (23.4 Hz/bin), *no* equalize, bins 1–5 = 23–117 Hz, own AutoGain |

The MilkDrop chain is ported from the actual code, not its comments (`fft.cpp` comments
recommend octave bands; the code uses linear thirds — `sender.py` cites the source lines
inline). Deprecated-but-stable: these keep pre-v3 shaders exact via the milk texture's
legacy texel block.

### Spectrum / chroma / beat / stereo / key

From the same 4096-sample FFT (`SpectrumAnalyzer`, 11.7 Hz/bin, ~85 ms window):

| field | what it is |
|---|---|
| `spec[128]` | real spectrum, 30 Hz–16 kHz, dB-normalized 0..1 (v2 shipped 512 — the wall can't use that density). Hybrid **linear-then-log** axis: first 23 bins linear at one FFT bin each (→~300 Hz), the rest log to 16 kHz, so every bin carries real FFT data (no interpolated low-end holes). Axis constants printed in the startup banner. |
| `chroma[12]` | pitch-class energy C…B (fold FFT bins → 12 semitones, peak-normalized) — for color-by-key |
| `centroid, flux, flatness, rolloff, crest` | spectral descriptors: brightness, onset strength (auto-gained), tonal-vs-noisy, 85%-rolloff, peakiness |
| `bpm, beat_phase, bar_phase, beat_conf` | the **predictive beat tracker** (`beat.py` — clean-room DAFx-09, *not* MilkDrop): comb-filterbank tempo induction + cumulative-score alignment + a slew-limited PLL beat grid. `beat_phase` ramps 0→1 and hits 1.0 **on** the predicted beat (anticipatory, not reactive); `bar_phase` spans 4 beats. BEAT/DOWNBEAT pulses ride `flags`. Validate with `uv run beat.py` (offline click-track harness) |
| `width, pan` | stereo correlation (mono↔anti-phase) and L/R balance, time-domain from the right channel the band path ignores |
| `key_idx, key_conf` | musical key via Krumhansl-Schmuckler correlation on a ~3 s chroma EMA: 0–11 = C…B major, 12–23 = minor. A mood signal — gate on the confidence |

`flags` carries a 4-bit **source_domain** (0 = audio) plus the BEAT/DOWNBEAT bits, so
non-audio senders (SDR, telemetry) self-label.

## Packet — v3, little-endian, 2996 bytes

`PACKET_FMT = "<IHHIf7f2f8f24f24f8f7f512f128f12f5f4f2f2f"` in `sender.py`; mirrored
(with the size asserted) in `rayglow/feed/receiver.py` and `rayglow/fake_sender.py` —
`tools/feed_check.py` asserts all three stay identical and roundtrips every field.
**Any layout change must land on all ends and bump `version`.** The receiver dispatches
on `(version, exact byte length)` and accepts v0 (556 B), v1 (564 B, `sub = bass` for
v0), v2 (4236 B) and v3 — older senders report zeros/defaults for fields they don't
carry. At ~3 KB × 60 Hz ≈ 0.18 MB/s it's a single UDP datagram (well under the ~65 KB
datagram ceiling).

The header + legacy fields keep their v2 byte positions, so the receiver's common parse
path is version-agnostic; everything after `sub_att` is v3's own layout.

| offset | type | field |
|---|---|---|
| 0 | uint32 | magic = `0x4D494C4B` ("MILK") |
| 4 | uint16 | version = 3 |
| 6 | uint16 | flags — bits 0–3 source_domain, bit 4 BEAT, bit 5 DOWNBEAT |
| 8 | uint32 | seq — wraps at 2³²; receiver drops stale/reordered (RFC 1982-style compare) |
| 12 | float32 | t — sender monotonic seconds |
| 16 | float32[7] | bass, mid, treb, bass_att, mid_att, treb_att, vol (legacy) |
| 44 | float32[2] | sub, sub_att (legacy) |
| 52 | float32[8] | band_imm — b0…b7 |
| 84 | float32[24] | band_env — [band][tier], band-major |
| 180 | float32[24] | band_theta — [band][tier], band-major, mod 200π |
| 276 | float32[8] | band_onset |
| 308 | float32[7] | vol_imm, vol_env[3], vol_theta[3] |
| 336 | float32[512] | wave |
| 2384 | float32[128] | spec — hybrid lin/log spectrum, dB-norm 0..1 |
| 2896 | float32[12] | chroma — pitch classes C…B |
| 2944 | float32[5] | centroid, flux, flatness, rolloff, crest |
| 2964 | float32[4] | bpm, beat_phase, bar_phase, beat_conf |
| 2980 | float32[2] | width, pan |
| 2988 | float32[2] | key_idx, key_conf |
| **2996** | | **total** |

Receiver discipline (`rayglow/feed/receiver.py`): bind once, drain the socket
nonblocking every frame keeping only the highest seq, never block the render loop. No
packet for 0.5 s → `FeatureState` switches to a synthesized fallback (bands breathing
around 1.0, fake beat) so the panel never freezes or goes dark.

## How the features reach shaders

`rayglow.render` exposes the feed as `iChannel` textures, bound per-shader with comment
directives (`// iChannel0: milk`) or `--channelN` flags:

- **`milk`** — a 16×3 RGBA32F texture (float: the >1.0 spikes survive; read with
  `texelFetch`). **Row 0** cols 0–7 = b0…b7, col 8 = vol: `.x` imm, `.y/.z/.w` the three
  envelope tiers. **Row 1**, same cols: `.x/.y/.z` theta0/1/2, `.w` onset. **Row 2**
  globals: (0,2) tempo block, (1,2) beat/downbeat pulses + key, (2,2) descriptors,
  (3,2) crest + stereo, (4–6,2) chroma, (7,2) feed health (`pkt_age`/`live`/
  `source_domain`), and (9–11,2) the **legacy block** — the classic
  bass/mid/treb/vol/sub scalars, so pre-v3 shaders port 1:1. Full map:
  `rayglow/render/textures.py` (`MilkChannel`); reference cards that draw every float as
  a labeled bar — `milk-verbose.glsl` (bands/motion) and `milk-features.glsl` (globals).
- **`spectrum`** — a 128×1 RGBA32F texture: `.x` the real spectrum on the hybrid
  lin/log axis (30 Hz–16 kHz, full float range, no waveform round-trip); `.y`/`.z`
  smooth Catmull-Rom curves through the 8 band values (imm / env1) on the same axis —
  a ready-made "fit of the bands" for silky spectrum-shaped visuals. Sample like a 1-D
  LUT; reference card `milk-spectrum.glsl`. Zero on a v0/v1 sender.
- **`audio`** — the shadertoy.com-faithful 512×2 spectrum/waveform texture, so stock
  shaders work unmodified. The spectrum row is rebuilt Pi-side from `wave` (Web-Audio dB
  scaling, 0.8 smoothing) — faithful to the site, which means heavily compressed: bass
  pins near 1.0 whenever music plays. For dynamic band values use `milk`; for a real
  spectral shape use `spectrum`.

Division of labor: the **desktop** (this daemon) computes everything — FFTs, AutoGain,
envelopes, thetas, beat, key — on its steady clock; the **Pi** just packs texels.
(Pre-v3 the Pi derived d/dt/env/theta itself, inheriting packet-arrival jitter.)

### Porting pre-v3 shaders (old 13×1 texel → new 16×3)

| old | new |
|---|---|
| (0,0)/(1,0)/(2,0) bass/mid/treb `.x` imm | (9,2) `.x/.y/.z` (legacy, identical) — or bands (2,0)/(5,0)/(7,0) `.x` |
| (3,0) vol, (4,0) sub `.x` | (9,2)`.w`, (10,2)`.w` (legacy) |
| `.y` att (bass/mid/treb/sub) | (10,2)`.x/.y/.z`, (11,2)`.x` (legacy) |
| `.w` env (per band) | `.y` env0 of the nearest v3 band: b2/b5/b7/vol(8,0)/b0 |
| `.z` ddt | dropped — one-sided analog: row 1 `.w` onset of the nearest band |
| (5,0) thetas b/m/t/v, (6,0).x sub theta | (2,1)/(5,1)/(7,1)/(8,1)/(0,1) `.x` theta0 |
| (6,0) `.y` age `.z` live `.w` source | (7,2) `.x/.y/.z` |
| (7,0) descriptors | (2,2) |
| (8,0) crest/bpm/phase/conf | (3,2)`.x` + (0,2)`.x/.y/.w` |
| (9,0) beat/downbeat/width/pan | (1,2)`.x/.y` + (3,2)`.y/.z` |
| (10–12,0) chroma | (4–6,2) |
