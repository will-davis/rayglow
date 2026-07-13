# sender — RayGLow desktop feature daemon

The broadcast half of RayGLow. `sender.py` (one file) captures whatever the desktop is
playing, reduces it to a handful of per-frame audio features, and unicasts them over UDP
at ~60 Hz to the Pi, which renders them as GLSL and drives a 256×64 HUB75 wall (via an
RP2350 over SPI). For the system-level picture and the renderer, see the
[top-level README](../README.md).

This is a standalone uv project: it shares no code with the `rayglow` package — only the
**packet contract** (mirrored in `rayglow/feed/receiver.py`). The daemon only ever
*runs* on the desktop, since it's capturing desktop audio.

## Running

```fish
cd sender
uv run sender.py                  # monitor of default sink -> 192.168.0.50:5005
uv run sender.py --list-sources   # enumerate pulse sources
uv run sender.py --source NAME    # capture a specific source instead
uv run sender.py --debug          # adds raw pre-normalization band energies
uv run sender.py --host H --port N --fps N
```

Prints a 1 Hz status line (`sub/bass/mid/treb` as `imm/att`, plus `vol`). Python ≥3.13,
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

| field | what it is |
|---|---|
| `bass`, `mid`, `treb` | MilkDrop's three bands: 576-sample window → Hann → 1024-pt FFT → 512 bins × log-equalize → three equal *linear* thirds of the bottom half ≈ 0–4 / 4–8 / 8–12 kHz at 48 kHz |
| `vol` | sum of the three, own AutoGain |
| `wave[512]` | mono waveform window, resampled 576 → 512, ±1.0 (v2 widened it from 128) |
| `sub`, `sub_att` | **v1, not MilkDrop**: true sub-bass. MilkDrop's "bass" covers 0–4 kHz with a log-equalize that suppresses the lowest bins ~90× — a subwoofer is invisible in it. `sub` uses a separate 2048-sample FFT (23.4 Hz/bin), *no* equalize, bins 1–5 = 23–117 Hz, own AutoGain |

The MilkDrop chain is ported from the actual code, not its comments (`fft.cpp` comments
recommend octave bands; the code uses linear thirds — `sender.py` cites the source lines
inline). Band placement was validated with 110 Hz / 6 kHz / 10 kHz sine tones.

### v2 feed (richer, additive)

The MilkDrop band path above is unchanged — v2 adds a **separate, larger 4096-sample FFT**
(`SpectrumAnalyzer`, 11.7 Hz/bin, ~85 ms window — real low-end resolution while the bands
stay snappy on their own 576/1024 FFT) feeding five new groups:

| field | what it is |
|---|---|
| `spec[512]` | real spectrum, 30 Hz–16 kHz, dB-normalized 0..1 — a spectral *shape* (the v0/v1 wire carried none). Hybrid **linear-then-log** axis: first ~162 bins linear at one FFT bin each (→~1.9 kHz), the rest log to 16 kHz, so every bin carries real FFT data (no interpolated low-end holes). Remap to all-log in the shader if wanted. |
| `chroma[12]` | pitch-class energy C…B (fold FFT bins → 12 semitones, peak-normalized) — for color-by-key |
| `centroid, flux, flatness, rolloff, crest` | spectral descriptors: brightness, onset strength (auto-gained), tonal-vs-noisy, 85%-rolloff, peakiness |
| `bpm, beat_phase, beat_conf` | tempo from autocorrelating the flux onset envelope (`BeatTracker`); the per-frame BEAT/DOWNBEAT pulses ride the `flags` field |
| `width, pan` | stereo correlation (mono↔anti-phase) and L/R balance, time-domain from the right channel the band path ignores |

`flags` (always 0 through v1) now carries a 4-bit **source_domain** (0 = audio) plus the
BEAT/DOWNBEAT bits, so non-audio senders (SDR, telemetry) self-label.

## Packet — v2, little-endian, 4236 bytes

`PACKET_FMT = "<IHHIf7f2f512f512f12f5f3f2f"` in `sender.py`; mirrored (with the size
asserted) in `rayglow/feed/receiver.py`. **Any layout change must land on both ends and
bump `version`.** The receiver dispatches on `(version, exact byte length)` and accepts
v0 (556 B), v1 (564 B, `sub = bass` for v0), and v2 — older senders report zeros/defaults
for the v2-only fields. At ~4.2 KB × 60 Hz ≈ 0.25 MB/s it's a single UDP datagram (well
under the ~65 KB datagram ceiling — beyond that would need multi-datagram reassembly).

Note the v2 field order differs from v1 (`sub`/`sub_att` move ahead of `wave`, which grew
to 512), so v2 is parsed against its own layout — not as a v1 tail-append.

| offset | type | field |
|---|---|---|
| 0 | uint32 | magic = `0x4D494C4B` ("MILK") |
| 4 | uint16 | version = 2 |
| 6 | uint16 | flags — bits 0–3 source_domain, bit 4 BEAT, bit 5 DOWNBEAT |
| 8 | uint32 | seq — wraps at 2³²; receiver drops stale/reordered (RFC 1982-style compare) |
| 12 | float32 | t — sender monotonic seconds |
| 16 | float32[7] | bass, mid, treb, bass_att, mid_att, treb_att, vol |
| 44 | float32 | sub |
| 48 | float32 | sub_att |
| 52 | float32[512] | wave |
| 2100 | float32[512] | spec — hybrid lin/log spectrum, dB-norm 0..1 |
| 4148 | float32[12] | chroma — pitch classes C…B |
| 4196 | float32[5] | centroid, flux, flatness, rolloff, crest |
| 4216 | float32[3] | bpm, beat_phase, beat_conf |
| 4228 | float32[2] | width, pan |
| **4236** | | **total** |

Receiver discipline (`rayglow/feed/receiver.py`): bind once, drain the socket
nonblocking every frame keeping only the highest seq, never block the render loop. No
packet for 0.5 s → `FeatureState` switches to a synthesized fallback (bands breathing
around 1.0, fake beat) so the panel never freezes or goes dark.

## How the features reach shaders

`rayglow.render` exposes the feed as `iChannel` textures, bound per-shader with comment
directives (`// iChannel0: milk`) or `--channelN` flags:

- **`milk`** — a 13×1 RGBA32F texture (float: the >1.0 spikes survive; read with
  `texelFetch`). Texels 0–4 are bass/mid/treb/vol/sub; per band `.x` = imm, `.y` = att,
  plus two Pi-derived signals: `.z` = d/dt (signed onset detector) and `.w` = imm
  through a ~125 ms envelope. Texel 5 holds integrated phase per band ("music time":
  `theta += imm·dt`, for seamless `sin(theta·k)` motion), texel 6 the sub phase plus
  feed health (`pkt_age` seconds, `live` 0/1, `source_domain`). **v2 scalars:** texel 7
  spectral descriptors (centroid/flux/flatness/rolloff), texel 8 crest + tempo
  (`bpm/240`, beat_phase, beat_conf), texel 9 beat/downbeat pulses + stereo (width/pan),
  texels 10–12 the 12-bin chroma. Full map: `rayglow/render/textures.py`
  (`MilkChannel`); reference cards that draw every float as a labeled bar —
  `milk-verbose.glsl` (v1 bands/derived) and `milk-features.glsl` (v2 scalars).
- **`spectrum`** — a 512×1 RGBA32F texture: the v2 feed's real spectrum on a hybrid
  lin/log axis (30 Hz–16 kHz, full float range, no waveform round-trip). Sample like a 1-D LUT;
  reference card `milk-spectrum.glsl` draws it plus the chroma strip. Zero on a v0/v1
  sender.
- **`audio`** — the shadertoy.com-faithful 512×2 spectrum/waveform texture, so stock
  shaders work unmodified. The spectrum row is rebuilt Pi-side from `wave` (Web-Audio dB
  scaling, 0.8 smoothing) — faithful to the site, which means heavily compressed: bass
  pins near 1.0 whenever music plays. For dynamic band values use `milk`; for a real
  spectral shape use `spectrum`.

Division of labor on derived signals: the **desktop** (this daemon) computes what needs
full-resolution audio (FFTs, band split, AutoGain); the **Pi** computes what only needs
the scalars themselves (d/dt, envelopes, phase integration) in `MilkChannel.update()`,
keeping the wire format small and renderer-agnostic.
