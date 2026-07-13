#!/usr/bin/env python3
"""Fake feature sender — music-free test harness for the renderer.

Sends v3 feature packets (the same struct as sender/sender.py) over unicast
UDP at ~60 Hz, but with *synthesized* features instead of real audio.  The
legacy bands (bass = beat pulses at BPM, mid = wandering noise, treb =
sweeps + sparkle, plus a punchy sub) run through MilkDrop's EXACT auto-gain
(vis_milk2/plugin.cpp:8750), so the renderer sees imm_rel/avg_rel values
that hover ~1.0 and spike on hits — same semantics real audio produces.
The v3 groups are synthesized with the same machinery as the real sender:
8 raw band energies through their own AutoGains, flywheel envelopes and
theta accumulators integrated exactly like sender.py's Flywheel, per-band
onsets pulsing on the (known) beat, a 128-bin spectrum, a rotating key.
The beat block is driven by the known --bpm so beat_phase ramps exactly
into each beat and BEAT/DOWNBEAT pulse on time — ground truth for testing
beat-reactive shaders.  Point the renderer at this when no music plays.

Standalone on purpose (stdlib + numpy only, no package imports) so it can
run anywhere.  It predates sender/sender.py and was the original executable
spec for the wire format; the two now share the contract in
rayglow/feed/receiver.py, and tools/feed_check.py asserts their format
strings stay identical.  ENV_TIERS / THETA_WRAP / Flywheel are deliberate
small duplicates of sender/sender.py — keep the constants in lockstep (the
fmt is checked mechanically; the tier constants are not).

Run:  ~/venv/bin/python -m rayglow.fake_sender [--host H] [--port P] [--bpm N]
"""
import argparse
import math
import random
import socket
import struct
import time

import numpy as np

# ---- packet (v3; layout + field table in rayglow/feed/receiver.py) ------------
WAVE_SAMPLES = 512
SPEC_OUT = 128
CHROMA_N = 12
N_BANDS = 8
N_TIERS = 3
PACKET_FMT = ("<IHHIf7f2f"
              + f"{N_BANDS}f{N_BANDS * N_TIERS}f{N_BANDS * N_TIERS}f{N_BANDS}f"
              + f"{1 + 2 * N_TIERS}f"
              + f"{WAVE_SAMPLES}f{SPEC_OUT}f{CHROMA_N}f"
              + "5f4f2f2f")
MAGIC = 0x4D494C4B              # "MILK"
VERSION = 3
FLAG_BEAT = 0x10
FLAG_DOWNBEAT = 0x20
assert struct.calcsize(PACKET_FMT) == 2996

# ---- defaults ----------------------------------------------------------------
HOST = "127.0.0.1"
PORT = 5005
FPS = 60.0
BPM = 120.0

# Flywheel constants — MUST mirror sender/sender.py (ENV_TIERS, THETA_WRAP).
ENV_TIERS = [(8.0, 8.0),    # tier0: symmetric ~125 ms
             (16.0, 2.0),   # tier1: punchy — ~60 ms attack / ~500 ms decay
             (6.0, 0.5)]    # tier2: heavy  — ~150 ms attack / ~2 s decay
THETA_WRAP = 200.0 * math.pi


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


class Flywheel:
    """Mirror of sender/sender.py's Flywheel — envelope tiers + thetas."""

    def __init__(self, n):
        self.env = np.ones((n, N_TIERS))
        self.theta = np.zeros((n, N_TIERS))

    def update(self, imm, dt):
        for k, (attack, decay) in enumerate(ENV_TIERS):
            e = self.env[:, k]
            rate = np.where(imm > e, attack, decay)
            e += (imm - e) * np.minimum(1.0, rate * dt)
        integ = np.column_stack((imm, self.env[:, 1], self.env[:, 2]))
        self.theta = (self.theta + integ * dt) % THETA_WRAP


def synth_bands(t, bpm):
    """Raw (pre-normalization) LEGACY band energies.  Arbitrary scales on
    purpose — the auto-gain must normalize them away, just like real audio."""
    beat_phase = (t * bpm / 60.0) % 1.0
    bass = 2.0 + 18.0 * math.exp(-7.0 * beat_phase) + random.uniform(0, 0.8)
    mid = 5.0 + 2.5 * math.sin(t * 0.9) + random.uniform(0, 2.5)
    treb = 1.5 + 1.2 * math.sin(t * 0.37) + random.uniform(0, 1.0) \
        + (3.0 if random.random() < 0.02 else 0.0)          # occasional sparkle
    vol = bass + mid + treb
    # v1 sub: tighter decay than bass, near-zero floor between kicks
    sub = 0.3 + 30.0 * math.exp(-11.0 * beat_phase) + random.uniform(0, 0.2)
    return bass, mid, treb, vol, sub


def synth_bands8(t, bpm):
    """Raw v3 band energies + raw per-band onsets, arbitrary scales.
    Low bands pump on the beat with decreasing punch, mids wander, the top
    two sparkle — every band moves, so a reference card shows life in all 8.
    Onsets: a spike right at the beat for the low half, random sparkle up top.
    """
    beat_phase = (t * bpm / 60.0) % 1.0
    kick = math.exp(-9.0 * beat_phase)
    raw = np.empty(N_BANDS)
    onset = np.empty(N_BANDS)
    for i in range(N_BANDS):
        punch = max(0.0, 1.0 - i / 4.0)              # b0 strongest, gone by b4
        wander = 1.0 + 0.4 * math.sin(t * (0.3 + 0.17 * i) + 1.7 * i)
        sparkle = (2.0 + i) if (i >= 6 and random.random() < 0.03) else 0.0
        raw[i] = (0.5 + 25.0 * punch * kick + 2.0 * wander + sparkle) \
            * (1.0 + 0.1 * i)
        onset[i] = (20.0 * punch if beat_phase < 0.05 else 0.0) \
            + sparkle + random.uniform(0.0, 0.3)
    return raw, onset


def synth_wave(t, bpm, x):
    """Mono waveform window, ±1: bass sine swelling on the beat + treble fuzz."""
    beat_phase = (t * bpm / 60.0) % 1.0
    swell = 0.35 + 0.55 * math.exp(-5.0 * beat_phase)
    wave = swell * np.sin(2.0 * x + t * 4.0) \
        + 0.15 * np.sin(11.0 * x + t * 23.0) \
        + 0.05 * np.random.uniform(-1, 1, WAVE_SAMPLES)
    return np.clip(wave, -1.0, 1.0).astype(np.float32)


_SPEC_X = np.linspace(0.0, 1.0, SPEC_OUT, dtype=np.float32)


def synth_v3(t, bpm, prev_beat_phase):
    """Synthesize the spectrum/chroma/descriptor/beat/stereo/key groups +
    the flags bits.  The beat block is driven by the known bpm: beat_phase
    ramps 0->1 into each beat (the v3 predictive semantics), bar_phase spans
    4 beats, and BEAT/DOWNBEAT pulse exactly on time."""
    beats_f = t * bpm / 60.0
    beat_phase = beats_f % 1.0
    beat_count = int(beats_f)
    pulse = math.exp(-6.0 * beat_phase)

    # spectrum: two formants sliding around + a bass bump that pumps on beats
    f1, f2 = 0.12 + 0.10 * math.sin(t * 0.23), 0.5 + 0.18 * math.sin(t * 0.17 + 2.0)
    spec = (0.85 * np.exp(-((_SPEC_X - f1) ** 2) / 0.002)
            + 0.55 * np.exp(-((_SPEC_X - f2) ** 2) / 0.01)
            + 0.35 * np.exp(-_SPEC_X / 0.05) * pulse + 0.06)
    spec = np.clip(spec, 0.0, 1.0).astype(np.float32)

    # chroma: a slowly rotating dominant pitch class
    c = np.arange(CHROMA_N, dtype=np.float32)
    chroma = (0.4 + 0.6 * np.cos(
        2.0 * np.pi * (c / CHROMA_N - (t * 0.07 % 1.0))) ** 4).astype(np.float32)

    centroid = 0.2 + 0.12 * math.sin(t * 0.3)
    flux = 0.4 + 1.8 * pulse
    flatness = 0.2 + 0.08 * math.sin(t * 0.5)
    rolloff = 0.4 + 0.18 * math.sin(t * 0.27)
    crest = 8.0 + 5.0 * pulse

    beat = beat_phase < prev_beat_phase            # wrapped this frame -> a beat
    downbeat = beat and (beat_count % 4 == 0)
    flags = (FLAG_BEAT if beat else 0) | (FLAG_DOWNBEAT if downbeat else 0)
    bar_phase = ((beat_count % 4) + beat_phase) / 4.0

    width = 0.6 + 0.3 * math.sin(t * 0.13)
    pan = 0.4 * math.sin(t * 0.19)

    key_idx = float(int(t / 8.0) % 24)             # stroll through all 24 keys
    key_conf = 0.9

    desc = (centroid, flux, flatness, rolloff, crest)
    beat4 = (bpm, beat_phase, bar_phase, 0.85)
    stereo = (width, pan)
    key = (key_idx, key_conf)
    return spec, chroma, desc, beat4, stereo, key, flags, beat_phase


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default=HOST)
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--bpm", type=float, default=BPM)
    ap.add_argument("--fps", type=float, default=FPS)
    args = ap.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    gains = [AutoGain() for _ in range(5)]      # legacy: bass, mid, treb, vol, sub
    band_gains = [AutoGain() for _ in range(N_BANDS)]
    onset_gains = [AutoGain() for _ in range(N_BANDS)]
    volx_gain = AutoGain()
    fly = Flywheel(N_BANDS + 1)                 # channel 8 = vol
    x = np.linspace(0.0, 2.0 * np.pi, WAVE_SAMPLES, dtype=np.float32)

    print(f"fake_sender -> {args.host}:{args.port} @ {args.fps:.0f} Hz, "
          f"{args.bpm:.0f} BPM, v{VERSION} (ctrl-c to stop)")

    seq = 0
    prev_beat_phase = 0.0
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
        dt = min(max(now - prev_now, 0.0), 3.0 / args.fps)
        prev_now = now

        bands = synth_bands(t, args.bpm)
        rels = [g.update(imm, args.fps) for g, imm in zip(gains, bands)]
        (bass, bass_att), (mid, mid_att), (treb, treb_att), (vol, _), \
            (sub, sub_att) = rels
        wave = synth_wave(t, args.bpm, x)

        raw8, onset_raw8 = synth_bands8(t, args.bpm)
        band_imm = np.array([band_gains[i].update(raw8[i], args.fps)[0]
                             for i in range(N_BANDS)])
        band_onset = np.array([onset_gains[i].update(onset_raw8[i],
                                                     args.fps)[0]
                               for i in range(N_BANDS)])
        vol_imm = volx_gain.update(float(raw8.sum()), args.fps)[0]
        fly.update(np.append(band_imm, vol_imm), dt)

        spec, chroma, desc, beat4, stereo, key, flags, prev_beat_phase = \
            synth_v3(t, args.bpm, prev_beat_phase)

        pkt = struct.pack(PACKET_FMT, MAGIC, VERSION, flags, seq & 0xFFFFFFFF, t,
                          bass, mid, treb, bass_att, mid_att, treb_att, vol,
                          sub, sub_att,
                          *band_imm,
                          *fly.env[:N_BANDS].ravel(),
                          *fly.theta[:N_BANDS].ravel(),
                          *band_onset,
                          vol_imm, *fly.env[N_BANDS], *fly.theta[N_BANDS],
                          *wave, *spec, *chroma, *desc, *beat4, *stereo, *key)
        sock.sendto(pkt, (args.host, args.port))
        seq += 1

        if now - last_print >= 1.0:
            bands_s = " ".join(f"{v:3.1f}" for v in band_imm)
            print(f"t={t:7.1f}s seq={seq:6d}  b=[{bands_s}] vol={vol_imm:4.2f} "
                  f"legacy bass={bass:5.2f}/{bass_att:4.2f} vol={vol:4.2f}")
            last_print = now


if __name__ == "__main__":
    main()
