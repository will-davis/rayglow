#!/usr/bin/env python3
"""RayGLow desktop feature daemon — the broadcast half of the LED-panel visualizer.

Captures the PipeWire monitor of the default sink (whatever the desktop is
playing), extracts per-frame audio features, and sends ~3 KB v3 feature
packets over unicast UDP at ~60 Hz to the Pi, which renders on a 256x64 HUB75
wall.

Receiving end (the `rayglow` package on the Raspberry Pi 5; same git repo):
  - rayglow.feed.receiver — the other half of the packet contract (accepts
    v0..v3, nonblocking latest-wins drain)
  - rayglow.feed.features — FeatureState: latest packet values + synth fallback
  - rayglow.render — THE renderer: Shadertoy-dialect GLSL on the Pi's GPU
    (headless EGL + GLES3).  This packet's features enter shaders as
    iChannel textures: 'milk' (16x3 float — bands/envelopes/thetas/globals;
    texel map in rayglow/render/textures.py MilkChannel, live reference
    cards in rayglow/render/presets/milk-*.glsl), 'spectrum' (128x1 float)
    and 'audio' (512x2 Shadertoy-style, rebuilt from this packet's wave).
  - rayglow/fake_sender.py — the music-free test harness speaking the same
    struct, for exercising the renderer without audio.
  - tools/feed_check.py — packet-contract roundtrip check + --live monitor.

LEGACY LAYER (unchanged — a faithful MilkDrop port).  The classic bands
still ship in the packet header, still computed by the exact ported path,
so old shaders/senders stay calibrated.  Each band is normalized by its own
running average: 1.0 = "typical for this song right now", hits spike 2-3.
  - FFT front-end (vis_milk2/fft.cpp): 576-sample window, left channel,
    Hann envelope (InitEnvelopeTable, power=1), zero-padded 1024-pt FFT,
    512 magnitude bins scaled by the log equalize table (InitEqualizeTable):
        equalize[i] = -0.02 * ln((512 - i) / 512)
  - Band split (vis_milk2/plugin.cpp:8736, DoCustomSoundAnalysis): bottom
    half of the spectrum in three equal LINEAR thirds — bins [0:85],
    [85:170], [170:256].  (fft.cpp's comments recommend octave bands; the
    actual code never uses them.  We replicate the code, not the comment.)
  - AutoGain (plugin.cpp:8750): identical to fake_sender.py.
  - v1 sub band (ours, non-MilkDrop): 2048-pt FFT (23.4 Hz/bin), NO
    equalize, bins 1..5 = 23-117 Hz, own AutoGain — MilkDrop's "bass"
    (0-4 kHz, lowest bins equalized ~90x down) can't see the subwoofer.

v3 LAYER (the primary feed; 2026-07 overhaul).  v2's spectrum/chroma/
descriptors/stereo carry over; its beat fields are recomputed by a better
tracker; the band feed is rebuilt around what actually proved useful on
the wall (envelopes + theta accumulators):
  - bands     8 log-spaced bands 20 Hz..16 kHz (BAND_EDGES_V3), each with
              its own AutoGain (same "1.0 = typical" contract).  b0-b3
              (<500 Hz) read the 4096-pt spectrum FFT (11.7 Hz/bin — real
              low-end resolution); b4-b7 the snappy 576-window FFT (12 ms).
              NO equalize on either: the per-band AGC does the leveling.
  - flywheels 3 envelope tiers per band (ENV_TIERS): tier0 = the classic
              symmetric ~125 ms lag; tier1/tier2 asymmetric (fast attack,
              slow decay) — "momentum" a kick spins up and silence bleeds
              off slowly.  Computed here on the steady sender clock, not
              on the Pi against jittery packet arrival times (the v2 way).
  - thetas    3 "music time" phase accumulators per band: theta0 += imm*dt,
              theta1 += env1*dt, theta2 += env2*dt, wrapping at 200*pi —
              iTime replacements giving rotation ACCELERATION, not steps.
  - onsets    per-band half-wave-rectified spectral flux, own AutoGains —
              one-sided attack spikes (the useful half of v2's dropped d/dt).
  - vol       the same imm/env/theta treatment for the overall level.
  - spectrum  128 bins (512 in v2), 30 Hz..16 kHz hybrid lin/log axis,
              dB-normalized 0..1 (see _spec_split; the axis constants are
              printed at startup — mirror them into milk-spectrum.glsl).
  - chroma    12 pitch classes + key_idx/key_conf (Krumhansl-Schmuckler).
  - beat      predictive DAFx-09-style tracker (beat.py — clean-room, NOT
              MilkDrop): bpm, beat_phase (anticipatory 0->1 ramp hitting
              1.0 ON the predicted beat), bar_phase (4 beats), confidence.
              The per-frame BEAT/DOWNBEAT pulses ride the `flags` field.
  - stereo    width (L/R correlation) + pan, from the right channel that
              the band path ignores.
The `flags` u16 carries a 4-bit source_domain (0=audio) plus BEAT/DOWNBEAT
bits, so non-audio senders (SDR, telemetry) are self-labeled.

Packet layout: PACKET_FMT below, mirrored in rayglow/feed/receiver.py; full
field table in this directory's README.md.  (docs/design-history/ holds the
project record: the MilkDrop reverse-engineering, the v0 ancestor, and the
v2->v3 overhaul rationale.)

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

from beat import BeatTracker

# ---- packet v3 — contract mirrored in rayglow/feed/receiver.py + fake_sender.py
# Layout (little-endian, no padding):
#   header   magic u32, version u16, flags u16, seq u32, t f32
#   legacy   bass mid treb bass_att mid_att treb_att vol            (7f)
#   legacy   sub sub_att                                            (2f)
#   bands    band_imm[8]   8 log bands, AGC'd, clamped              (8f)
#   env      band_env[8][3]   flywheel tiers, band-major            (24f)
#   theta    band_theta[8][3] wrapping phases, band-major           (24f)
#   onset    band_onset[8]  per-band rectified flux, AGC'd          (8f)
#   vol      vol_imm vol_env[3] vol_theta[3]                        (7f)
#   wave     mono waveform, 512 samples, ±1.0                       (512f)
#   spec     hybrid lin/log spectrum, 30Hz..16kHz, dB-norm 0..1     (128f)
#   chroma   12 pitch-class energies, C..B, peak-normalized         (12f)
#   desc     centroid flux flatness rolloff crest                   (5f)
#   beat     bpm beat_phase bar_phase beat_conf                     (4f)
#   stereo   width pan                                              (2f)
#   key      key_idx (0-11 C..B major, 12-23 minor) key_conf        (2f)
# The header + 7 legacy floats + (sub, sub_att) keep their v1/v2 SEMANTICS —
# still computed by the untouched MilkDrop path.  The receiver dispatches on
# (version, exact byte length) so v0/v1/v2 senders still parse against their
# own layouts (see receiver.VERSIONS).
WAVE_SAMPLES = 512
SPEC_OUT = 128                  # streamed spectrum bins (512 in v2)
CHROMA_N = 12                   # pitch classes
N_BANDS = 8                     # v3 log-spaced bands
N_TIERS = 3                     # flywheel envelope tiers per band
PACKET_FMT = ("<IHHIf7f2f"
              + f"{N_BANDS}f{N_BANDS * N_TIERS}f{N_BANDS * N_TIERS}f{N_BANDS}f"
              + f"{1 + 2 * N_TIERS}f"
              + f"{WAVE_SAMPLES}f{SPEC_OUT}f{CHROMA_N}f"
              + "5f4f2f2f")
MAGIC = 0x4D494C4B              # "MILK"
VERSION = 3
SOURCE_AUDIO = 0                # flags bits 0-3: source_domain
FLAG_BEAT = 0x10               # flags bit 4: onset this frame
FLAG_DOWNBEAT = 0x20           # flags bit 5: every 4th beat
assert struct.calcsize(PACKET_FMT) == 2996

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

# ---- v3 bands / flywheel / key ---------------------------------------------------
# 8 log-spaced bands, very-low sub to air.  Edges in Hz; bin slices are derived
# with searchsorted, never hardcoded.  b0-b3 (<500 Hz) read the 4096-pt spectrum
# FFT (11.7 Hz/bin — b0's 20 Hz edge lands at its first usable bin, ~23 Hz, the
# same floor as the legacy sub band); b4-b7 read the 576-window/1024-pt FFT
# (46.9 Hz/bin, 12 ms window — snappy where time resolution matters).  NO
# equalize on either path: each band has its own AutoGain, which IS the leveling.
BAND_EDGES_V3 = np.array([20.0, 60.0, 120.0, 250.0, 500.0,
                          1000.0, 2500.0, 6000.0, 16000.0])
N_BANDS_LOW = 4                 # bands read from the 4096-pt FFT
_HI_FFT_HZ = np.arange(SPEC_BINS) * (SAMPLE_RATE / NFREQ)    # 1024-FFT bin Hz
_LO_SLICES = [slice(*np.searchsorted(_FFT_HZ, BAND_EDGES_V3[i:i + 2]))
              for i in range(N_BANDS_LOW)]
_HI_SLICES = [slice(*np.searchsorted(_HI_FFT_HZ, BAND_EDGES_V3[i:i + 2]))
              for i in range(N_BANDS_LOW, N_BANDS)]

# Flywheel envelope tiers: (attack_hz, decay_hz) first-order lag rates applied
# to the AGC'd imm — rate = attack while rising, decay while falling.  The
# asymmetric tiers are the "momentum": a kick slams the envelope up fast and it
# sails down slowly.  THE tune-on-the-wall table; keep len == N_TIERS.
ENV_TIERS = [(8.0, 8.0),    # tier0: symmetric ~125 ms — the classic env feel
             (16.0, 2.0),   # tier1: punchy — ~60 ms attack / ~500 ms decay
             (6.0, 0.5)]    # tier2: heavy  — ~150 ms attack / ~2 s decay
assert len(ENV_TIERS) == N_TIERS
THETA_WRAP = 200.0 * np.pi      # sin(theta*k) seamless for k a multiple of 0.01
BAND_IMM_CLAMP = 16.0           # AGC blow-up guard (quiet band + sudden hit)
# The low bands' magnitudes come from the 4096-sample window, the high bands'
# from the 576-sample window — different absolute scales (~ the window sums).
# Weight the low sum so vol_imm's raw input mixes the two FFTs comparably.
VOL_LOW_COMP = float(ENVELOPE.sum() / SPEC_ENVELOPE.sum())   # ~0.14


class SpectrumAnalyzer:
    """Larger dedicated FFT -> streamed spectrum, chroma, and spectral
    descriptors.  Holds one frame of previous magnitudes for spectral flux."""

    def __init__(self):
        self._prev_mag = None

    def update(self, window_left):
        """window_left: SPEC_WINDOW left-channel samples (oldest-first).
        Also returns the raw magnitudes — BandAnalyzer's low bands read them."""
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

        return spec, chroma, centroid, flatness, rolloff, crest, flux, mag


class BandAnalyzer:
    """The 8 v3 band energies + per-band onset flux, read from the two FFTs
    the sender already runs (no new transform).  Owns its own previous-
    magnitude copies so its flux state doesn't entangle with
    SpectrumAnalyzer's global flux."""

    def __init__(self):
        self._prev_lo = None
        self._prev_hi = None

    def update(self, mag_lo, mag_hi):
        """mag_lo: 4096-pt FFT magnitudes, mag_hi: 1024-pt FFT magnitudes.
        Returns (raw[8], onset_raw[8]) — band energy sums and half-wave-
        rectified per-bin flux sums, both pre-AGC."""
        if self._prev_lo is None:
            self._prev_lo, self._prev_hi = mag_lo, mag_hi
        d_lo = np.maximum(0.0, mag_lo - self._prev_lo)
        d_hi = np.maximum(0.0, mag_hi - self._prev_hi)
        self._prev_lo, self._prev_hi = mag_lo, mag_hi
        raw = np.empty(N_BANDS)
        onset = np.empty(N_BANDS)
        for i, sl in enumerate(_LO_SLICES):
            raw[i] = mag_lo[sl].sum()
            onset[i] = d_lo[sl].sum()
        for i, sl in enumerate(_HI_SLICES):
            raw[N_BANDS_LOW + i] = mag_hi[sl].sum()
            onset[N_BANDS_LOW + i] = d_hi[sl].sum()
        return raw, onset


class Flywheel:
    """v3 envelope tiers + theta accumulators for n channels (8 bands + vol).

    env[:, k] chases the AGC'd imm with tier-k ballistics (ENV_TIERS):
        env += (imm - env) * min(1, rate*dt)   rate = attack rising / decay falling
    theta integrates (imm, env1, env2) — "music time" phases advancing
    ~1 rad/s at typical level, accelerating when the music does, wrapping at
    THETA_WRAP (200*pi) so sin(theta*k) stays seamless for k a multiple of
    0.01.  float64 internally (precision at the wrap); cast at pack time.
    """

    def __init__(self, n):
        self.env = np.ones((n, N_TIERS))       # 1.0 = "typical", not zero
        self.theta = np.zeros((n, N_TIERS))

    def update(self, imm, dt):
        for k, (attack, decay) in enumerate(ENV_TIERS):
            e = self.env[:, k]
            rate = np.where(imm > e, attack, decay)
            e += (imm - e) * np.minimum(1.0, rate * dt)
        integ = np.column_stack((imm, self.env[:, 1], self.env[:, 2]))
        self.theta = (self.theta + integ * dt) % THETA_WRAP


KEY_NAMES = [pc + m for m in ("", "m") for pc in
             ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")]


class KeyDetector:
    """Musical key from chroma: Pearson correlation of a ~3 s chroma EMA
    against the 24 Krumhansl-Schmuckler key profiles (12 major + 12 minor).
    Returns (key_idx, key_conf): idx 0-11 = C..B major, 12-23 = C..B minor;
    conf = the winning correlation, 0..1.  Slow-moving mood signal, not a
    precision instrument — gate on key_conf."""

    _MAJ = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09,
                     2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
    _MIN = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53,
                     2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
    EMA_RATE = 1.0 / 3.0            # 1/s — ~3 s horizon

    def __init__(self):
        profs = np.array([np.roll(self._MAJ, k) for k in range(12)]
                         + [np.roll(self._MIN, k) for k in range(12)])
        profs -= profs.mean(axis=1, keepdims=True)
        self._profs = profs / np.linalg.norm(profs, axis=1, keepdims=True)
        self._ema = np.zeros(12)

    def update(self, chroma, dt):
        self._ema += (chroma - self._ema) * min(1.0, self.EMA_RATE * dt)
        x = self._ema - self._ema.mean()
        n = np.linalg.norm(x)
        if n < 1e-9:
            return 0.0, 0.0
        r = self._profs @ (x / n)
        k = int(np.argmax(r))
        return float(k), float(np.clip(r[k], 0.0, 1.0))


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
    """576 left-channel samples -> (bass, mid, treb, vol, mag): the legacy
    band energies plus the raw pre-equalize magnitudes (the v3 high bands
    read those — no second transform).

    Exact port of FFT::time_to_frequency_domain + DoCustomSoundAnalysis;
    the equalize multiply lands on a copy, so legacy numerics are unchanged.
    """
    mag = np.abs(np.fft.rfft(window_left * ENVELOPE, n=NFREQ))[:SPEC_BINS]
    spec = mag * EQUALIZE
    bands = [float(spec[BAND_EDGES[i]:BAND_EDGES[i + 1]].sum()) for i in range(3)]
    return bands[0], bands[1], bands[2], bands[0] + bands[1] + bands[2], mag


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
    band_gains = [AutoGain() for _ in range(N_BANDS)]
    onset_gains = [AutoGain() for _ in range(N_BANDS)]
    volx_gain = AutoGain()                      # v3 vol (all-band, cross-FFT)
    wave_x = np.linspace(0.0, WINDOW - 1, WAVE_SAMPLES)
    spectrum = SpectrumAnalyzer()
    bands_an = BandAnalyzer()
    fly = Flywheel(N_BANDS + 1)                 # channel 8 = vol
    beats = BeatTracker(args.fps)
    keys = KeyDetector()

    print(f"sender: {source} -> {args.host}:{args.port} @ {args.fps:.0f} Hz (ctrl-c to stop)")
    print(f"packet: v{VERSION}, {struct.calcsize(PACKET_FMT)} B   spectrum axis: "
          f"OUT={SPEC_OUT} NLIN={SPEC_NLIN} FC={_SPEC_FC:.4f} R={_SPEC_R:.7f} "
          f"(mirror into presets/milk-spectrum.glsl)")

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
        bass_r, mid_r, treb_r, vol_r, mag_hi = analyze(frames[:, 0])  # legacy path
        rels = [gains[i].update(imm, args.fps)
                for i, imm in enumerate((bass_r, mid_r, treb_r, vol_r))]
        (bass, bass_att), (mid, mid_att), (treb, treb_att), (vol, _) = rels

        raw_sub = analyze_sub(cap.latest(SUB_WINDOW)[:, 0])
        sub, sub_att = gains[4].update(raw_sub, args.fps)

        mono = frames.mean(axis=1)                        # mono mix for the drawn wave
        wave = np.clip(np.interp(wave_x, np.arange(WINDOW), mono),
                       -1.0, 1.0).astype(np.float32)

        # ---- spectrum / chroma / descriptors (v2 heritage) -----------------
        spec, chroma, centroid, flatness, rolloff, crest, raw_flux, mag_lo = \
            spectrum.update(cap.latest(SPEC_WINDOW)[:, 0])
        flux, _ = gains[5].update(raw_flux, args.fps)     # onset, 1.0 = typical
        width, pan = analyze_stereo(frames)

        # ---- v3 bands + flywheels + beat + key ------------------------------
        raw8, onset_raw8 = bands_an.update(mag_lo, mag_hi)
        band_imm = np.array([min(band_gains[i].update(raw8[i], args.fps)[0],
                                 BAND_IMM_CLAMP) for i in range(N_BANDS)])
        band_onset = np.array([min(onset_gains[i].update(onset_raw8[i],
                                                         args.fps)[0],
                                   BAND_IMM_CLAMP) for i in range(N_BANDS)])
        vol_raw = raw8[:N_BANDS_LOW].sum() * VOL_LOW_COMP \
            + raw8[N_BANDS_LOW:].sum()
        vol_imm = min(volx_gain.update(vol_raw, args.fps)[0], BAND_IMM_CLAMP)
        # measured wall dt, clamped: a stall must not dump hours into theta
        fly.update(np.append(band_imm, vol_imm),
                   min(max(dt, 0.0), 3.0 / args.fps))
        bpm, beat_phase, bar_phase, beat_conf, beat, downbeat = \
            beats.update(flux, dt)
        key_idx, key_conf = keys.update(chroma, dt)

        flags = SOURCE_AUDIO
        if beat:
            flags |= FLAG_BEAT
        if downbeat:
            flags |= FLAG_DOWNBEAT

        pkt = struct.pack(PACKET_FMT, MAGIC, VERSION, flags, seq & 0xFFFFFFFF, t,
                          bass, mid, treb, bass_att, mid_att, treb_att, vol,
                          sub, sub_att,
                          *band_imm,
                          *fly.env[:N_BANDS].ravel(),
                          *fly.theta[:N_BANDS].ravel(),
                          *band_onset,
                          vol_imm, *fly.env[N_BANDS], *fly.theta[N_BANDS],
                          *wave, *spec, *chroma,
                          centroid, flux, flatness, rolloff, crest,
                          bpm, beat_phase, bar_phase, beat_conf,
                          width, pan, key_idx, key_conf)
        sock.sendto(pkt, (args.host, args.port))
        seq += 1

        if now - last_print >= 1.0:
            bands_s = " ".join(f"{v:3.1f}" for v in band_imm)
            line = (f"t={t:7.1f}s seq={seq:6d}  b=[{bands_s}] vol={vol_imm:4.2f}"
                    f"  bpm={bpm:5.1f}/{beat_conf:.2f} bar={bar_phase:.2f} "
                    f"key={KEY_NAMES[int(key_idx)]:>3}/{key_conf:.2f} "
                    f"cen={centroid:.2f} pan={pan:+.2f}")
            if args.debug:
                line += (f"  legacy=(sub={sub:4.2f} bass={bass:4.2f} "
                         f"mid={mid:4.2f} treb={treb:4.2f} vol={vol:4.2f})"
                         f" raw0={raw8[0]:8.3f} onset0={onset_raw8[0]:8.3f}"
                         f" flux={raw_flux:8.3f}")
            print(line)
            last_print = now


if __name__ == "__main__":
    main()
