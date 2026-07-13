#!/usr/bin/env python3
"""Fake feature sender — music-free test harness for the renderer.

Sends v2 feature packets (the same struct as sender/sender.py) over unicast
UDP at ~60 Hz, but with *synthesized* features instead of real audio: band
energies (bass = beat pulses at BPM, mid = wandering noise, treb = sweeps +
sparkle, plus a punchy sub) run through MilkDrop's EXACT auto-gain
(vis_milk2/plugin.cpp:8750), so the renderer sees imm_rel/avg_rel values that
hover ~1.0 and spike on hits — same semantics real audio produces. The v2
groups (spectrum, chroma, descriptors, beat, stereo) are synthesized too, the
beat driven by the known --bpm so beat_phase lands exactly on the beat. Point
the Pi at this when you want to exercise a shader with no music playing.

Standalone on purpose (stdlib + numpy only, no package imports) so it can run
anywhere. It predates sender/sender.py and was the original executable spec
for the wire format; the two now share the contract in
rayglow/feed/receiver.py. (Historical packet record:
docs/design-history/project-milk-pi.md §5, which describes the v0 ancestor.)

Run:  ~/venv/bin/python -m rayglow.fake_sender [--host H] [--port P] [--bpm N]
"""
import argparse
import math
import random
import socket
import struct
import time

import numpy as np

# ---- packet (v2; layout + field table in rayglow/feed/receiver.py) ------------
WAVE_SAMPLES = 512
SPEC_OUT = 512
CHROMA_N = 12
PACKET_FMT = ("<IHHIf7f2f" + f"{WAVE_SAMPLES}f" + f"{SPEC_OUT}f"
              + f"{CHROMA_N}f" + "5f3f2f")
MAGIC = 0x4D494C4B              # "MILK"
VERSION = 2
FLAG_BEAT = 0x10
FLAG_DOWNBEAT = 0x20
assert struct.calcsize(PACKET_FMT) == 4236

# ---- defaults ----------------------------------------------------------------
HOST = "127.0.0.1"
PORT = 5005
FPS = 60.0
BPM = 120.0


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


def synth_bands(t, bpm):
    """Raw (pre-normalization) band energies.  Arbitrary scales on purpose —
    the auto-gain must normalize them away, just like real audio."""
    beat_phase = (t * bpm / 60.0) % 1.0
    bass = 2.0 + 18.0 * math.exp(-7.0 * beat_phase) + random.uniform(0, 0.8)
    mid = 5.0 + 2.5 * math.sin(t * 0.9) + random.uniform(0, 2.5)
    treb = 1.5 + 1.2 * math.sin(t * 0.37) + random.uniform(0, 1.0) \
        + (3.0 if random.random() < 0.02 else 0.0)          # occasional sparkle
    vol = bass + mid + treb
    # v1 sub: tighter decay than bass, near-zero floor between kicks
    sub = 0.3 + 30.0 * math.exp(-11.0 * beat_phase) + random.uniform(0, 0.2)
    return bass, mid, treb, vol, sub


def synth_wave(t, bpm, x):
    """Mono waveform window, ±1: bass sine swelling on the beat + treble fuzz."""
    beat_phase = (t * bpm / 60.0) % 1.0
    swell = 0.35 + 0.55 * math.exp(-5.0 * beat_phase)
    wave = swell * np.sin(2.0 * x + t * 4.0) \
        + 0.15 * np.sin(11.0 * x + t * 23.0) \
        + 0.05 * np.random.uniform(-1, 1, WAVE_SAMPLES)
    return np.clip(wave, -1.0, 1.0).astype(np.float32)


_SPEC_X = np.linspace(0.0, 1.0, SPEC_OUT, dtype=np.float32)


def synth_v2(t, bpm, prev_beat_phase):
    """Synthesize the v2 feature groups + the flags bits.

    Returns (spec[512], chroma[12], descriptors(5), beat(3), stereo(2),
    flags, beat_phase) — the beat is driven by the known bpm so beat_phase
    lands on the beat and BEAT/DOWNBEAT pulse exactly on time.
    """
    beat_phase = (t * bpm / 60.0) % 1.0
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

    beat_phase_out = beat_phase
    beat = beat_phase < prev_beat_phase            # wrapped this frame -> a beat
    downbeat = beat and (int(t * bpm / 60.0) % 4 == 0)
    flags = (FLAG_BEAT if beat else 0) | (FLAG_DOWNBEAT if downbeat else 0)

    width = 0.6 + 0.3 * math.sin(t * 0.13)
    pan = 0.4 * math.sin(t * 0.19)

    desc = (centroid, flux, flatness, rolloff, crest)
    beat3 = (bpm, beat_phase_out, 0.85)
    stereo = (width, pan)
    return spec, chroma, desc, beat3, stereo, flags, beat_phase_out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default=HOST)
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--bpm", type=float, default=BPM)
    ap.add_argument("--fps", type=float, default=FPS)
    args = ap.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    gains = [AutoGain() for _ in range(5)]      # bass, mid, treb, vol, sub
    x = np.linspace(0.0, 2.0 * np.pi, WAVE_SAMPLES, dtype=np.float32)

    print(f"fake_sender -> {args.host}:{args.port} @ {args.fps:.0f} Hz, "
          f"{args.bpm:.0f} BPM (ctrl-c to stop)")

    seq = 0
    prev_beat_phase = 0.0
    t0 = time.monotonic()
    next_tick = t0
    last_print = t0
    while True:
        now = time.monotonic()
        if now < next_tick:
            time.sleep(next_tick - now)
            now = time.monotonic()
        next_tick += 1.0 / args.fps
        t = now - t0

        bands = synth_bands(t, args.bpm)
        rels = [g.update(imm, args.fps) for g, imm in zip(gains, bands)]
        (bass, bass_att), (mid, mid_att), (treb, treb_att), (vol, _), \
            (sub, sub_att) = rels
        wave = synth_wave(t, args.bpm, x)
        spec, chroma, desc, beat3, stereo, flags, prev_beat_phase = \
            synth_v2(t, args.bpm, prev_beat_phase)

        pkt = struct.pack(PACKET_FMT, MAGIC, VERSION, flags, seq & 0xFFFFFFFF, t,
                          bass, mid, treb, bass_att, mid_att, treb_att, vol,
                          sub, sub_att, *wave, *spec, *chroma, *desc, *beat3,
                          *stereo)
        sock.sendto(pkt, (args.host, args.port))
        seq += 1

        if now - last_print >= 1.0:
            print(f"t={t:7.1f}s seq={seq:6d}  bass={bass:5.2f}/{bass_att:4.2f} "
                  f"mid={mid:5.2f}/{mid_att:4.2f} treb={treb:5.2f}/{treb_att:4.2f} "
                  f"vol={vol:4.2f}")
            last_print = now


if __name__ == "__main__":
    main()
