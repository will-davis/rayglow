"""FeatureState: the audio->visual interface (band scalars + waveform + v3 feed).

Holds the latest packet values; when no packets arrive for a while, synthesizes
a gentle fallback (band values breathing around 1.0, sine waveform, a moving
spectrum) so the display never freezes or goes dark.

v3 is the primary feed: 8 log-spaced bands with 3 flywheel envelope tiers and
3 theta phase accumulators each (all derived on the SENDER's steady clock —
nothing is integrated here anymore), per-band onsets, a vol block with the
same treatment, a predictive beat block (+bar_phase), and key detection.
The v2-era fields (spectrum, chroma, descriptors, stereo) carry over; the
legacy bass/mid/treb/vol/sub scalars are still shipped by every sender.
Older senders leave the fields they don't carry at these neutral defaults.
"""
import math

import numpy as np

from . import config

WAVE_SAMPLES = 512
SPEC_BINS = 128         # v3 wire size; self.spec holds whatever length arrived
CHROMA_N = 12
N_BANDS = 8
N_TIERS = 3
_THETA_WRAP = 628.3185307179586    # 200*pi, mirrors the sender's THETA_WRAP


class FeatureState:
    def __init__(self):
        # imm_rel-style band energies: 1.0 = "typical for this song right now"
        self.bass = self.mid = self.treb = 1.0
        self.bass_att = self.mid_att = self.treb_att = 1.0
        self.vol = 1.0
        self.sub = self.sub_att = 1.0   # v1: true 23-117Hz band (v0: = bass)
        self.wave = np.zeros(WAVE_SAMPLES, dtype=np.float32)
        # v3 feed: bands/envelopes/thetas at "typical"/rest, so an old sender
        # renders a calm flat state rather than black
        self.bands = np.ones(N_BANDS, dtype=np.float32)
        self.band_env = np.ones((N_BANDS, N_TIERS), dtype=np.float32)
        self.band_theta = np.zeros((N_BANDS, N_TIERS), dtype=np.float32)
        self.band_onset = np.zeros(N_BANDS, dtype=np.float32)
        self.vol_imm = 1.0
        self.vol_env = np.ones(N_TIERS, dtype=np.float32)
        self.vol_theta = np.zeros(N_TIERS, dtype=np.float32)
        self.bar_phase = 0.0
        self.key_idx = 0.0      # 0-11 C..B major, 12-23 minor
        self.key_conf = 0.0
        # v2-era feed (neutral defaults; older senders never set these)
        self.spec = np.zeros(SPEC_BINS, dtype=np.float32)
        self.chroma = np.zeros(CHROMA_N, dtype=np.float32)
        self.centroid = self.flux = self.flatness = 0.0
        self.rolloff = self.crest = 0.0
        self.bpm = 0.0
        self.beat_phase = self.beat_conf = 0.0
        self.beat = self.downbeat = False
        self.width = self.pan = 0.0
        self.source_domain = 0
        self.t = 0.0          # engine clock, seconds since start
        self.frame = 0
        self.progress = 0.0   # 0..1 through the current preset's rotation slot
        self.fps = 60.0       # EMA of measured fps (presets may use it)
        self._last_pkt_time = None

    @property
    def pkt_age(self):
        """Seconds since the last real packet (1e6 if none ever arrived)."""
        if self._last_pkt_time is None:
            return 1e6
        return self.t - self._last_pkt_time

    @property
    def live(self):
        """True if we're rendering from real packets (not synth fallback)."""
        return self.pkt_age < config.FALLBACK_AFTER

    def update(self, pkt, now, dt):
        self.t = now
        self.frame += 1
        if dt > 0:
            self.fps += 0.05 * (1.0 / dt - self.fps)

        if pkt is not None:
            self.bass, self.mid, self.treb = pkt["bass"], pkt["mid"], pkt["treb"]
            self.bass_att = pkt["bass_att"]
            self.mid_att = pkt["mid_att"]
            self.treb_att = pkt["treb_att"]
            self.vol = pkt["vol"]
            self.sub = pkt.get("sub", pkt["bass"])
            self.sub_att = pkt.get("sub_att", pkt["bass_att"])
            self.wave = pkt["wave"]
            self.source_domain = pkt.get("source_domain", 0)
            self.beat = pkt.get("beat", False)
            self.downbeat = pkt.get("downbeat", False)
            # newer-version arrays/scalars: keep last value (or the neutral
            # defaults) when an older packet omits them
            if pkt.get("bands") is not None:     # v3 band arrays travel together
                self.bands = pkt["bands"]
                self.band_env = pkt["band_env"]
                self.band_theta = pkt["band_theta"]
                self.band_onset = pkt["band_onset"]
                self.vol_env = pkt["vol_env"]
                self.vol_theta = pkt["vol_theta"]
            self.vol_imm = pkt.get("vol_imm", self.vol_imm)
            self.bar_phase = pkt.get("bar_phase", self.bar_phase)
            self.key_idx = pkt.get("key_idx", self.key_idx)
            self.key_conf = pkt.get("key_conf", self.key_conf)
            if pkt.get("spec") is not None:
                self.spec = pkt["spec"]
            if pkt.get("chroma") is not None:
                self.chroma = pkt["chroma"]
            self.centroid = pkt.get("centroid", self.centroid)
            self.flux = pkt.get("flux", self.flux)
            self.flatness = pkt.get("flatness", self.flatness)
            self.rolloff = pkt.get("rolloff", self.rolloff)
            self.crest = pkt.get("crest", self.crest)
            self.bpm = pkt.get("bpm", self.bpm)
            self.beat_phase = pkt.get("beat_phase", self.beat_phase)
            self.beat_conf = pkt.get("beat_conf", self.beat_conf)
            self.width = pkt.get("width", self.width)
            self.pan = pkt.get("pan", self.pan)
            self._last_pkt_time = now
        elif not self.live:
            self._synthesize(now)
        # else: recent packet exists — hold last values (one held frame is invisible)

    def _synthesize(self, t):
        """No-network fallback: slow LFOs hovering ~1.0 + a fake beat, sine wave.

        Mirrors the shape of fake_sender output so 'no network' degrades
        gracefully instead of to a dead screen.
        """
        beat = math.exp(-3.0 * ((t * 2.0) % 1.0))            # 120 BPM-ish pulse
        self.bass = 0.75 + 0.25 * math.sin(t * 0.41) + 0.9 * beat
        # sub: tighter to the beat than bass, near-silent between hits
        self.sub = 0.45 + 2.2 * math.exp(-6.0 * ((t * 2.0) % 1.0))
        self.sub_att = 0.7 + 0.9 * beat
        self.mid = 1.0 + 0.25 * math.sin(t * 0.73 + 1.0)
        self.treb = 1.0 + 0.30 * math.sin(t * 1.13 + 2.0)
        self.bass_att = 0.9 + 0.3 * math.sin(t * 0.40)
        self.mid_att = 1.0 + 0.2 * math.sin(t * 0.70 + 1.0)
        self.treb_att = 1.0 + 0.2 * math.sin(t * 1.10 + 2.0)
        self.vol = 1.0 + 0.5 * beat
        x = np.linspace(0.0, 2.0 * np.pi, WAVE_SAMPLES, dtype=np.float32)
        self.wave = (0.6 + 0.35 * beat) * np.sin(2.0 * x + t * 3.0) \
            * np.sin(0.5 * x).astype(np.float32)

        # v2 fallback: a couple of slow formants sliding across the spectrum,
        # a rotating chroma, a 120-BPM beat clock, and wandering stereo — so
        # the new channels animate without a network feed too.
        bins = np.linspace(0.0, 1.0, SPEC_BINS, dtype=np.float32)
        f1 = 0.15 + 0.12 * math.sin(t * 0.23)
        f2 = 0.55 + 0.20 * math.sin(t * 0.17 + 2.0)
        self.spec = (0.9 * np.exp(-((bins - f1) ** 2) / 0.002)
                     + 0.6 * np.exp(-((bins - f2) ** 2) / 0.01)
                     + 0.08).astype(np.float32) * (0.6 + 0.4 * beat)
        self.spec = np.clip(self.spec, 0.0, 1.0)
        c = np.arange(CHROMA_N, dtype=np.float32)
        self.chroma = (0.5 + 0.5 * np.cos(
            2.0 * np.pi * (c / CHROMA_N - (t * 0.1 % 1.0)))).astype(np.float32)
        self.centroid = 0.2 + 0.15 * math.sin(t * 0.3)
        self.flux = 0.5 + 1.5 * beat
        self.flatness = 0.2 + 0.1 * math.sin(t * 0.5)
        self.rolloff = 0.4 + 0.2 * math.sin(t * 0.27)
        self.crest = 8.0 + 4.0 * beat
        self.bpm = 120.0
        self.beat_phase = (t * 2.0) % 1.0
        self.beat_conf = 0.8
        self.beat = self.beat_phase < 0.05
        self.downbeat = self.beat and (int(t * 2.0) % 4 == 0)
        self.width = 0.6 + 0.3 * math.sin(t * 0.13)
        self.pan = 0.4 * math.sin(t * 0.19)
        self.source_domain = 0

        # v3 fallback: the low bands pump on the same 120-BPM clock with
        # punch fading out by b4, mids wander, tiers smear the pulse with
        # progressively more "momentum".  Thetas are CLOSED-FORM integrals
        # of those rates (no dt bookkeeping — _synthesize must stay
        # stateless): a linear term plus a per-beat surge for the punchy
        # bands, so low-band rotation visibly kicks with the fake beat.
        i = np.arange(N_BANDS, dtype=np.float32)
        phase = np.float32(self.beat_phase)
        beats_done = np.float32(math.floor(t * 2.0))
        punch = np.maximum(0.0, 1.0 - i / 4.0)      # b0 hits hard, gone by b4
        wander = 1.0 + 0.25 * np.sin(t * (0.3 + 0.17 * i) + 1.7 * i)
        self.bands = (wander + 2.2 * punch
                      * np.exp(-6.0 * phase)).astype(np.float32)
        self.band_env = np.stack([
            wander + 1.8 * punch * np.exp(-4.0 * phase),      # ~125ms feel
            0.9 * wander + 1.2 * punch * np.exp(-1.5 * phase),  # punchy tier
            1.0 + 0.3 * np.sin(t * 0.23 + 0.8 * i),           # heavy tier
        ], axis=1).astype(np.float32)
        # integral of the beat pulse: one fixed surge per beat + the ramp
        # through the current one (integral of exp(-6*phase) over a beat)
        surge = (beats_done + (1.0 - np.exp(-6.0 * phase))) / 12.0
        self.band_theta = (np.stack([
            t * 1.0 + 2.2 * punch * surge,
            t * 0.9 + 1.2 * punch * surge,
            t * (0.7 + 0.05 * i),
        ], axis=1) % _THETA_WRAP).astype(np.float32)
        sparkle = 2.0 if (t * 7.3) % 1.0 < 0.06 else 0.0
        self.band_onset = (3.0 * punch * np.exp(-20.0 * phase)).astype(np.float32)
        self.band_onset[6:] += np.float32(sparkle)
        self.vol_imm = 1.0 + 0.5 * beat
        self.vol_env = np.array([1.0 + 0.4 * beat,
                                 1.0 + 0.25 * math.exp(-1.5 * float(phase)),
                                 1.0 + 0.1 * math.sin(t * 0.19)], np.float32)
        self.vol_theta = np.array([t * 1.1, t * 0.95, t * 0.8],
                                  np.float32) % np.float32(_THETA_WRAP)
        self.bar_phase = ((int(t * 2.0) % 4) + self.beat_phase) / 4.0
        self.key_idx = float(int(t / 8.0) % 24)     # stroll through the keys
        self.key_conf = 0.7
