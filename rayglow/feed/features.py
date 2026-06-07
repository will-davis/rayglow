"""FeatureState: the audio->visual interface (MilkDrop's six scalars + waveform).

Holds the latest packet values; when no packets arrive for a while, synthesizes
a gentle fallback (band values breathing around 1.0, sine waveform) so the
display never freezes or goes dark.
"""
import math

import numpy as np

from . import config

WAVE_SAMPLES = 128


class FeatureState:
    def __init__(self):
        # imm_rel-style band energies: 1.0 = "typical for this song right now"
        self.bass = self.mid = self.treb = 1.0
        self.bass_att = self.mid_att = self.treb_att = 1.0
        self.vol = 1.0
        self.sub = self.sub_att = 1.0   # v1: true 23-117Hz band (v0: = bass)
        self.wave = np.zeros(WAVE_SAMPLES, dtype=np.float32)
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
