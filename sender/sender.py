#!/usr/bin/env python3
"""RayGLow desktop feature daemon — the broadcast half of the LED-panel visualizer.

Captures the PipeWire monitor of the default sink (whatever the desktop is
playing), extracts per-frame audio features, and sends ~4.2 KB v2 feature
packets over unicast UDP at ~60 Hz to the Pi, which renders on a 256x32 HUB75
matrix.

Receiving end (the `rayglow` package on the Raspberry Pi 5 at 192.168.0.50; 
same git repo as this file:
  - rayglow.feed.receiver — the other half of the packet contract (accepts
    v0+v1, nonblocking latest-wins drain)
  - rayglow.feed.features — FeatureState: latest packet values + synth fallback
  - rayglow.render — THE renderer: Shadertoy-dialect GLSL on the Pi's
    VideoCore VI GPU (headless EGL + GLES3).  This packet's features enter
    shaders as iChannel textures: 'milk' (8x1 float — bands + Pi-derived
    signals; texel map in rayglow/render/textures.py MilkChannel, live
    reference card in rayglow/render/presets/milk-verbose.glsl) and 'audio'
    (512x2 Shadertoy-style spectrum/waveform rebuilt from this packet's
    wave[128]).
  - rayglow/fake_sender.py — the music-free test harness speaking the same
    struct, for exercising the renderer without audio.

The analysis chain is based on Milkdrop, expanded for higher resolution.
Each band normalized by its own running average, 1.0 = "typical for this
song right now", hits spike 2-3 — are what every shader downstream is
calibrated against, so the port stays exact:
  - FFT front-end (vis_milk2/fft.cpp): 576-sample window, left channel,
    Hann envelope (InitEnvelopeTable, power=1), zero-padded 1024-pt FFT,
    512 magnitude bins scaled by the log equalize table (InitEqualizeTable):
        equalize[i] = -0.02 * ln((512 - i) / 512)
  - Band split (vis_milk2/plugin.cpp:8736, DoCustomSoundAnalysis): bottom
    half of the spectrum in three equal LINEAR thirds — bins [0:85],
    [85:170], [170:256].  (fft.cpp's comments recommend octave bands; the
    actual code never uses them.  We replicate the code, not the comment.)
  - AutoGain (plugin.cpp:8750): identical to fake_sender.py.

v1 extension: a true sub-bass band.  MilkDrop's "bass" is linear bins 
0..85 = 0-4kHz with a log-equalize that suppresses the lowest bins ~90x
 — subwoofer content is effectively invisible in it.  `sub` fixes
that: 2048-sample FFT (23.4 Hz/bin), raw magnitudes (no equalize), bins
1..5 = 23-117 Hz, own AutoGain.  Appended to the packet as (sub, sub_att).
In shaders it's milk-texture texel 4 (or band index 4 anywhere bands are
ordered bass/mid/treb/vol/sub).

v2 extension (richer feed; the band path above is UNCHANGED so the
existing shader library stays calibrated).  A separate, larger 4096-sample
FFT (SpectrumAnalyzer below; 11.7 Hz/bin — real low-end resolution, ~85ms
window) feeds five new feature groups appended to the packet:
  - spectrum  512 magnitude bins (30 Hz..16 kHz) on a hybrid lin/log axis,
              dB-normalized 0..1.  A true spectral SHAPE — the wire used to
              carry none.  (Hybrid axis: real FFT data in every bin, no
              interpolated low-end holes — see _SPEC_EDGES below.)
  - chroma    12 pitch-class energies (C..B), for color-by-key visuals.
  - descriptors  centroid (brightness), spectral flux (onset), flatness
              (tonal vs noisy), rolloff, crest.
  - beat      bpm + beat_phase(0..1) + confidence, from an autocorrelation
              tempo tracker on the flux onset envelope (BeatTracker below).
              The per-frame BEAT/DOWNBEAT pulses ride the `flags` field.
  - stereo    width (L/R correlation) + pan, from the right channel that the
              capture ring already holds but the band path ignores.
The `flags` u16 (always 0 through v1) now carries a 4-bit source_domain
(0=audio) plus BEAT/DOWNBEAT bits, so non-audio senders (SDR, telemetry)
are self-labeled instead of riding a convention.

Packet layout: PACKET_FMT below, mirrored in rayglow/feed/receiver.py; full
field table in this directory's README.md.  (docs/design-history/ holds the
original project record: the MilkDrop reverse-engineering, the v0 ancestor,
and the retired renderer.)

Run:  uv run sender.py [--host PI_IP] [--port 5005] [--source NAME]
      uv run sender.py --list-sources
      uv run sender.py --debug          # adds raw (pre-normalization) band prints

The Pi's address comes from --host, else $RAYGLOW_HOST, else the placeholder
default below — set one of those for your rig (see ../LOCAL-SETUP.example.md).
"""
import argparse
import os
import socket
import struct
import subprocess
import sys
import time

import numpy as np

# ---- packet v2 — contract mirrored in rayglow/feed/receiver.py + fake_sender.py
# Layout (little-endian, no padding):
#   header   magic u32, version u16, flags u16, seq u32, t f32
#   bands    bass mid treb bass_att mid_att treb_att vol            (7f)
#   sub      sub sub_att                                            (2f)
#   wave     mono waveform, 512 samples, ±1.0                       (512f)
#   spec     hybrid lin/log spectrum, 30Hz..16kHz, dB-norm 0..1     (512f)
#   chroma   12 pitch-class energies, C..B, peak-normalized         (12f)
#   desc     centroid flux flatness rolloff crest                   (5f)
#   beat     bpm beat_phase beat_conf                               (3f)
#   stereo   width pan                                              (2f)
# The header + 7 band floats + (sub,sub_att) keep their v1 SEMANTICS; the
# receiver dispatches on (version, exact byte length) so v0/v1 senders still
# parse against their own layouts (see receiver.VERSIONS).
WAVE_SAMPLES = 512
SPEC_OUT = 512                  # streamed log-spectrum bins
CHROMA_N = 12                   # pitch classes
PACKET_FMT = ("<IHHIf7f2f" + f"{WAVE_SAMPLES}f" + f"{SPEC_OUT}f"
              + f"{CHROMA_N}f" + "5f3f2f")
MAGIC = 0x4D494C4B              # "MILK"
VERSION = 2
SOURCE_AUDIO = 0                # flags bits 0-3: source_domain
FLAG_BEAT = 0x10               # flags bit 4: onset this frame
FLAG_DOWNBEAT = 0x20           # flags bit 5: every 4th beat
assert struct.calcsize(PACKET_FMT) == 4236

# ---- defaults ----------------------------------------------------------------
# The Pi's IP. Override per-rig with $RAYGLOW_HOST or --host; the literal here is
# only a placeholder so the daemon runs out-of-the-box (it won't reach a real Pi).
HOST = os.environ.get("RAYGLOW_HOST", "192.168.0.50")
PORT = int(os.environ.get("RAYGLOW_PORT", 5005))
FPS = 60.0

# ---- MilkDrop sound analysis constants ----------------------------------------
SAMPLE_RATE = 48000             # PipeWire native; MilkDrop used the device rate too
WINDOW = 576                    # vis_milk2/plugin.h:61 — fWave[2][576]
NFREQ = 1024                    # fft.cpp: NFREQ = samples_out*2
SPEC_BINS = NFREQ // 2          # 512 magnitude bins out
# Hann envelope over the 576 input samples (fft.cpp InitEnvelopeTable, power=1):
#   0.5 + 0.5*sin(i*2pi/576 - pi/2)  ==  0.5 - 0.5*cos(2pi*i/576)
ENVELOPE = (0.5 - 0.5 * np.cos(2.0 * np.pi * np.arange(WINDOW) / WINDOW)).astype(np.float32)
# Log equalize table (fft.cpp InitEqualizeTable):
EQUALIZE = (-0.02 * np.log((SPEC_BINS - np.arange(SPEC_BINS)) / SPEC_BINS)).astype(np.float32)
# Band edges: MY_FFT_SAMPLES*i/6 for i=0..3 (plugin.cpp:8739) -> 0, 85, 170, 256
BAND_EDGES = [SPEC_BINS * i // 6 for i in range(4)]

# ---- v1 sub band (ours, not MilkDrop's) ----------------------------------------
SUB_WINDOW = 2048               # 42.7ms at 48k -> 23.4 Hz/bin: can see the sub
SUB_ENVELOPE = (0.5 - 0.5 * np.cos(2.0 * np.pi * np.arange(SUB_WINDOW)
                                   / SUB_WINDOW)).astype(np.float32)
SUB_BINS = slice(1, 6)          # bins 1..5 = 23-117 Hz (skip 0: DC offset)

# ---- v2 spectrum / chroma / descriptor FFT (separate from the band FFT) ---------
# A longer window than analyze()'s 576: trades latency for frequency resolution
# (11.7 Hz/bin), which the displayed spectrum and chroma want and the snappy
# bands don't.  The MilkDrop band path stays on its own 576/1024 FFT untouched.
SPEC_WINDOW = 4096              # 85ms at 48k -> 11.7 Hz/bin
SPEC_ENVELOPE = (0.5 - 0.5 * np.cos(2.0 * np.pi * np.arange(SPEC_WINDOW)
                                    / SPEC_WINDOW)).astype(np.float32)
NYQUIST = SAMPLE_RATE / 2.0
_FFT_BINS = np.arange(SPEC_WINDOW // 2 + 1)
_FFT_HZ = (_FFT_BINS * SAMPLE_RATE / SPEC_WINDOW).astype(np.float32)

# Streamed spectrum: SPEC_OUT bands over [SPEC_FMIN, SPEC_FMAX] on a *hybrid
# linear-then-log* axis, chosen so every band carries real FFT data (max entropy
# on the wire) — no band is ever finer than the FFT can resolve.
#
# Why not pure log: the FFT resolves a uniform Δf = SAMPLE_RATE/SPEC_WINDOW
# (~11.7 Hz) at every frequency, but a log band below ~1.9 kHz is *narrower*
# than that, so ~160 low bands would capture zero FFT bins -> a comb of holes we
# could only fill by interpolation (faking data the FFT doesn't have).  Instead
# we spend the first SPEC_NLIN bands linearly at one FFT bin each (the densest
# *real* resolution the transform offers down low), then log-space the rest up to
# SPEC_FMAX.  The low end lands in a small block of slots; if a shader wants the
# classic full-log look it can remap/interpolate on the Pi, where it's free.
SPEC_FMIN, SPEC_FMAX = 30.0, 16000.0
_SPEC_DF = SAMPLE_RATE / SPEC_WINDOW            # FFT bin width, ~11.7 Hz
# Pick the linear/log split whose log segment lands on SPEC_FMAX with its first
# (narrowest) band still >= one FFT bin — a seamless join, zero empty bands.
def _spec_split():
    best = None
    for n_lin in range(1, SPEC_OUT):
        fc = SPEC_FMIN + n_lin * _SPEC_DF
        n_log = SPEC_OUT - n_lin
        if fc >= SPEC_FMAX or n_log < 1:
            continue
        r = (SPEC_FMAX / fc) ** (1.0 / n_log)  # ratio that hits FMAX exactly
        err = abs(fc * (r - 1.0) - _SPEC_DF)   # first log band vs one FFT bin
        if best is None or err < best[0]:
            best = (err, n_lin, fc, r)
    return best[1], best[2], best[3]

SPEC_NLIN, _SPEC_FC, _SPEC_R = _spec_split()   # ~162 linear bands, Fc ~1.9 kHz
_SPEC_EDGES = np.concatenate([
    SPEC_FMIN + _SPEC_DF * np.arange(SPEC_NLIN + 1),                 # linear
    _SPEC_FC * _SPEC_R ** np.arange(1, SPEC_OUT - SPEC_NLIN + 1),    # log
])
_SPEC_CENTERS = np.sqrt(_SPEC_EDGES[:-1] * _SPEC_EDGES[1:]).astype(np.float32)
_band_of_bin = np.searchsorted(_SPEC_EDGES, _FFT_HZ, side="right") - 1
_SPEC_MASK = (_band_of_bin >= 0) & (_band_of_bin < SPEC_OUT)
_SPEC_BAND = _band_of_bin[_SPEC_MASK]
_SPEC_COUNT = np.bincount(_SPEC_BAND, minlength=SPEC_OUT).astype(np.float32)
# Belt-and-suspenders: the hybrid axis yields zero empty bands by construction,
# but if the FFT params ever drift, any starved band reads its center magnitude
# by interpolation (below) rather than averaging-of-nothing -> 0.
_SPEC_EMPTY = _SPEC_COUNT == 0
_SPEC_COUNT[_SPEC_EMPTY] = 1.0                 # avoid /0 on any empty band
# dB-normalization: full-scale-sine peak ~ window.sum()/2; map a generous music
# range to 0..1.  Deliberately wider than the legacy audio-texture spectrum row
# (which clamps everything >-30dB to 1.0); shaders can further remap (see
# radio-waterfall.glsl's FLOOR/CEIL/GAMMA).
SPEC_REF = float(SPEC_ENVELOPE.sum()) / 2.0
SPEC_DB_FLOOR, SPEC_DB_CEIL = -90.0, -10.0

# Chroma: fold FFT bins in the musical range into 12 pitch classes (0=C).
# midi = 69 + 12*log2(f/440); pitch class = midi mod 12.  Low-octave chroma is
# inherently coarse even at 11.7 Hz/bin (sub-100Hz semitones aren't separable)
# — acceptable; a constant-Q transform is the upgrade path.
CHROMA_FMIN, CHROMA_FMAX = 55.0, 5000.0
_CHROMA_MASK = (_FFT_HZ >= CHROMA_FMIN) & (_FFT_HZ <= CHROMA_FMAX)
_CHROMA_PC = (np.round(69.0 + 12.0 * np.log2(_FFT_HZ[_CHROMA_MASK] / 440.0))
              .astype(int)) % 12


class SpectrumAnalyzer:
    """Larger dedicated FFT -> streamed spectrum, chroma, and spectral
    descriptors.  Holds one frame of previous magnitudes for spectral flux."""

    def __init__(self):
        self._prev_mag = None

    def update(self, window_left):
        """window_left: SPEC_WINDOW left-channel samples (oldest-first)."""
        mag = np.abs(np.fft.rfft(window_left * SPEC_ENVELOPE)).astype(np.float32)

        # Log-spaced spectrum (bandwidth-fair mean per band), dB-normalized.
        # Under-resolved low bands (no FFT bin landed in them) get the magnitude
        # interpolated at their center frequency, so the bottom ~40% isn't a comb
        # of zeros — see _SPEC_EMPTY above.
        sums = np.bincount(_SPEC_BAND, weights=mag[_SPEC_MASK], minlength=SPEC_OUT)
        band = sums.astype(np.float32) / _SPEC_COUNT
        band[_SPEC_EMPTY] = np.interp(_SPEC_CENTERS[_SPEC_EMPTY], _FFT_HZ, mag)
        db = 20.0 * np.log10(band / SPEC_REF + 1e-12)
        spec = np.clip((db - SPEC_DB_FLOOR) / (SPEC_DB_CEIL - SPEC_DB_FLOOR),
                       0.0, 1.0).astype(np.float32)

        # Chroma: pitch-class energy, peak-normalized to 0..1.
        chroma = np.bincount(_CHROMA_PC, weights=mag[_CHROMA_MASK],
                             minlength=12).astype(np.float32)
        chroma /= chroma.max() + 1e-9

        # Descriptors (all from the linear magnitude spectrum).
        msum = float(mag.sum()) + 1e-12
        centroid = float((_FFT_HZ * mag).sum()) / msum / NYQUIST   # brightness
        csum = np.cumsum(mag)
        roll_i = int(np.searchsorted(csum, 0.85 * csum[-1]))       # 85% rolloff
        rolloff = float(_FFT_HZ[min(roll_i, len(_FFT_HZ) - 1)]) / NYQUIST
        gmean = float(np.exp(np.log(mag + 1e-12).mean()))
        flatness = gmean / (float(mag.mean()) + 1e-12)             # tonal<->noisy
        crest = float(mag.max()) / (float(mag.mean()) + 1e-12)     # peakiness

        if self._prev_mag is None:
            self._prev_mag = mag
        flux = float(np.maximum(0.0, mag - self._prev_mag).sum())  # onset energy
        self._prev_mag = mag

        return spec, chroma, centroid, flatness, rolloff, crest, flux


class BeatTracker:
    """Tempo + beat phase from the spectral-flux onset envelope.

    Autocorrelates a few seconds of onset history to find the dominant period
    (BPM), advances a phase accumulator at that tempo, and nudges the phase
    toward onsets so beat_phase==0 lands on the beat.  This is the one
    heuristic component of the feed — good enough to lock onto steady music;
    a proper probabilistic tempo model is the upgrade path.
    """

    MIN_BPM, MAX_BPM = 70.0, 180.0
    HIST_SECS = 6.0
    RECOMPUTE_SECS = 0.5

    def __init__(self, fps):
        self.fps = fps
        self.N = max(8, int(self.HIST_SECS * fps))
        self.hist = np.zeros(self.N, np.float32)
        self.pos = 0
        self.filled = False
        self.bpm = 120.0
        self.phase = 0.0
        self.conf = 0.0
        self.beat_count = 0
        self._since_recompute = 0.0
        self._last_onset_t = -1.0

    def update(self, onset, dt, t):
        """onset: auto-gained spectral flux (~1.0 typical).  Returns
        (bpm, beat_phase 0..1, confidence, beat_flag, downbeat_flag)."""
        self.hist[self.pos] = onset
        self.pos = (self.pos + 1) % self.N
        if self.pos == 0:
            self.filled = True

        self.phase += self.bpm / 60.0 * dt        # advance at current tempo

        self._since_recompute += dt
        if self._since_recompute >= self.RECOMPUTE_SECS and self.filled:
            self._since_recompute = 0.0
            self._recompute()

        beat = downbeat = False
        if self.phase >= 1.0:
            self.phase -= 1.0
            beat = True
            self.beat_count += 1
            downbeat = (self.beat_count % 4) == 0

        # Re-sync: a strong onset should sit on a beat.  Pull a drifted phase
        # back toward 0 (gently, and rate-limited so we don't chase 16ths).
        if onset > 1.8 and (t - self._last_onset_t) > 0.25:
            self._last_onset_t = t
            self.phase *= 0.5

        return self.bpm, self.phase % 1.0, self.conf, beat, downbeat

    def _recompute(self):
        ordered = np.concatenate((self.hist[self.pos:], self.hist[:self.pos]))
        x = ordered - ordered.mean()
        ac = np.correlate(x, x, mode="full")[len(x) - 1:]   # ac[lag], lag>=0
        if ac[0] <= 1e-6:
            return
        lo = int(60.0 * self.fps / self.MAX_BPM)
        hi = min(int(60.0 * self.fps / self.MIN_BPM), len(ac) - 1)
        if hi <= lo:
            return
        k = lo + int(np.argmax(ac[lo:hi + 1]))
        self.bpm = 60.0 * self.fps / k
        self.conf = float(ac[k] / ac[0])


def analyze_stereo(frames):
    """(N, 2) stereo block -> (width, pan).

    width = L/R correlation (-1 anti-phase, 0 uncorrelated, +1 mono).
    pan   = RMS balance (-1 hard left, 0 center, +1 hard right).
    Time-domain, no FFT — uses the right channel the band path ignores.
    """
    left = frames[:, 0]
    right = frames[:, 1]
    el = float(np.sqrt(np.mean(left * left))) + 1e-9
    er = float(np.sqrt(np.mean(right * right))) + 1e-9
    width = float(np.mean(left * right)) / (el * er)
    pan = (er - el) / (er + el)
    return width, pan


def adjust_rate_to_fps(rate, fps1, actual_fps):
    """vis_milk2/utility.cpp:80 — convert a per-frame decay rate tuned at fps1
    to the equivalent rate at actual_fps."""
    return rate ** (fps1 / actual_fps)


class AutoGain:
    """MilkDrop's per-band normalization (plugin.cpp:8750).

    avg:      attack 0.2 rising / 0.5 falling (per-frame retention @30fps ref)
    long_avg: 0.9 for the first 50 frames (fast converge), then 0.992
    imm_rel = imm/long_avg, avg_rel = avg/long_avg  (1.0 = typical right now)
    """

    def __init__(self):
        self.avg = 0.0
        self.long_avg = 0.0
        self.frame = 0

    def update(self, imm, fps):
        rate = 0.2 if imm > self.avg else 0.5
        rate = adjust_rate_to_fps(rate, 30.0, fps)
        self.avg = self.avg * rate + imm * (1.0 - rate)

        rate = 0.9 if self.frame < 50 else 0.992
        rate = adjust_rate_to_fps(rate, 30.0, fps)
        self.long_avg = self.long_avg * rate + imm * (1.0 - rate)
        self.frame += 1

        if abs(self.long_avg) < 0.001:
            return 1.0, 1.0
        return imm / self.long_avg, self.avg / self.long_avg


def analyze(window_left):
    """576 left-channel samples -> (bass, mid, treb, vol) raw band energies.

    Exact port of FFT::time_to_frequency_domain + DoCustomSoundAnalysis.
    """
    spec = np.abs(np.fft.rfft(window_left * ENVELOPE, n=NFREQ))[:SPEC_BINS]
    spec *= EQUALIZE
    bands = [float(spec[BAND_EDGES[i]:BAND_EDGES[i + 1]].sum()) for i in range(3)]
    return bands[0], bands[1], bands[2], bands[0] + bands[1] + bands[2]


def analyze_sub(window_left):
    """2048 left-channel samples -> raw sub-bass energy (23-117 Hz).

    Unlike analyze(): longer window (resolution, not latency — 42.7ms),
    and NO equalize table, which would suppress these bins ~90x.
    """
    spec = np.abs(np.fft.rfft(window_left * SUB_ENVELOPE))
    return float(spec[SUB_BINS].sum())


class Capture:
    """Ring-buffered stereo capture from a PipeWire/Pulse source via PortAudio."""

    def __init__(self, source, sd):
        self.sd = sd
        self.ring = np.zeros((SAMPLE_RATE // 16, 2), dtype=np.float32)  # ~62 ms
        self.write_pos = 0
        self.filled = False

        # Targeting: the ALSA "pulse"/"default" plugin is a Pulse client, so
        # PULSE_SOURCE selects which source it records from.
        os.environ["PULSE_SOURCE"] = source
        device = None
        for i, dev in enumerate(sd.query_devices()):
            if dev["name"] == "pulse" and dev["max_input_channels"] >= 2:
                device = i
                break

        self.stream = sd.InputStream(
            device=device, samplerate=SAMPLE_RATE, channels=2, dtype="float32",
            blocksize=256, latency="low", callback=self._callback)
        self.stream.start()

    def _callback(self, indata, frames, time_info, status):
        n = len(indata)
        p = self.write_pos
        end = p + n
        if end <= len(self.ring):
            self.ring[p:end] = indata
        else:
            k = len(self.ring) - p
            self.ring[p:] = indata[:k]
            self.ring[:end - len(self.ring)] = indata[k:]
        self.write_pos = end % len(self.ring)
        if end >= len(self.ring):
            self.filled = True

    def latest(self, n):
        """Most recent n frames, oldest-first, shape (n, 2)."""
        p = self.write_pos
        idx = (np.arange(p - n, p)) % len(self.ring)
        return self.ring[idx]


def default_monitor():
    """Monitor source of the current default sink — i.e. 'what's playing'."""
    sink = subprocess.run(["pactl", "get-default-sink"],
                          capture_output=True, text=True, check=True).stdout.strip()
    return sink + ".monitor"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default=HOST)
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--fps", type=float, default=FPS)
    ap.add_argument("--source", default=None,
                    help="pulse source name (default: monitor of default sink)")
    ap.add_argument("--list-sources", action="store_true")
    ap.add_argument("--debug", action="store_true",
                    help="also print raw pre-normalization band energies")
    args = ap.parse_args()

    if args.list_sources:
        sys.exit(subprocess.run(["pactl", "list", "sources", "short"]).returncode)

    source = args.source or default_monitor()
    import sounddevice as sd          # import after PULSE_SOURCE decision-point
    cap = Capture(source, sd)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    gains = [AutoGain() for _ in range(6)]      # bass, mid, treb, vol, sub, flux
    wave_x = np.linspace(0.0, WINDOW - 1, WAVE_SAMPLES)
    spectrum = SpectrumAnalyzer()
    beats = BeatTracker(args.fps)

    print(f"sender: {source} -> {args.host}:{args.port} @ {args.fps:.0f} Hz (ctrl-c to stop)")

    seq = 0
    t0 = time.monotonic()
    next_tick = t0
    last_print = t0
    prev_now = t0
    while True:
        now = time.monotonic()
        if now < next_tick:
            time.sleep(next_tick - now)
            now = time.monotonic()
        next_tick += 1.0 / args.fps
        t = now - t0
        dt = now - prev_now
        prev_now = now

        frames = cap.latest(WINDOW)                       # (576, 2)
        raw = analyze(frames[:, 0])                       # left channel, like MilkDrop
        rels = [gains[i].update(imm, args.fps) for i, imm in enumerate(raw)]
        (bass, bass_att), (mid, mid_att), (treb, treb_att), (vol, _) = rels

        raw_sub = analyze_sub(cap.latest(SUB_WINDOW)[:, 0])
        sub, sub_att = gains[4].update(raw_sub, args.fps)

        mono = frames.mean(axis=1)                        # mono mix for the drawn wave
        wave = np.clip(np.interp(wave_x, np.arange(WINDOW), mono),
                       -1.0, 1.0).astype(np.float32)

        # ---- v2 features --------------------------------------------------
        spec, chroma, centroid, flatness, rolloff, crest, raw_flux = \
            spectrum.update(cap.latest(SPEC_WINDOW)[:, 0])
        flux, _ = gains[5].update(raw_flux, args.fps)     # onset, 1.0 = typical
        bpm, beat_phase, beat_conf, beat, downbeat = beats.update(flux, dt, t)
        width, pan = analyze_stereo(frames)

        flags = SOURCE_AUDIO
        if beat:
            flags |= FLAG_BEAT
        if downbeat:
            flags |= FLAG_DOWNBEAT

        pkt = struct.pack(PACKET_FMT, MAGIC, VERSION, flags, seq & 0xFFFFFFFF, t,
                          bass, mid, treb, bass_att, mid_att, treb_att, vol,
                          sub, sub_att, *wave, *spec, *chroma,
                          centroid, flux, flatness, rolloff, crest,
                          bpm, beat_phase, beat_conf, width, pan)
        sock.sendto(pkt, (args.host, args.port))
        seq += 1

        if now - last_print >= 1.0:
            line = (f"t={t:7.1f}s seq={seq:6d}  sub={sub:5.2f}/{sub_att:4.2f} "
                    f"bass={bass:5.2f}/{bass_att:4.2f} "
                    f"mid={mid:5.2f}/{mid_att:4.2f} treb={treb:5.2f}/{treb_att:4.2f} "
                    f"vol={vol:4.2f}  bpm={bpm:5.1f}/{beat_conf:.2f} "
                    f"cen={centroid:.2f} flat={flatness:.2f} pan={pan:+.2f}")
            if args.debug:
                line += (f"  raw=({raw[0]:6.3f} {raw[1]:6.3f} {raw[2]:6.3f} "
                         f"sub={raw_sub:6.3f} flux={raw_flux:6.3f})")
            print(line)
            last_print = now


if __name__ == "__main__":
    main()
