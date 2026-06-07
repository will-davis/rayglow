#!/usr/bin/env python3
"""Fake feature sender — the executable spec for the future desktop daemon.

Sends DRAFT v0 packets (project-milk-pi.md §5) over unicast UDP at ~60 Hz.
Synthesizes band energies (bass = beat pulses at BPM, mid = wandering noise,
treb = sweeps + sparkle), then runs MilkDrop's EXACT auto-gain
(vis_milk2/plugin.cpp:8750) so the renderer sees imm_rel/avg_rel values that
hover ~1.0 and spike on hits — same semantics real audio will produce.

Standalone on purpose (stdlib + numpy only, no package imports): this file
gets copied to will-desktop and its synth section swapped for PipeWire
capture + FFT.

Run:  ~/rgbvenv/bin/python milk/fake_sender.py [--host H] [--port P] [--bpm N]
"""
import argparse
import math
import random
import socket
import struct
import time

import numpy as np

# ---- packet (project-milk-pi.md §5, v1) ---------------------------------------
PACKET_FMT = "<IHHIf7f128f2f"   # v0 layout + (sub, sub_att) appended
MAGIC = 0x4D494C4B              # "MILK"
VERSION = 1
WAVE_SAMPLES = 128
assert struct.calcsize(PACKET_FMT) == 564

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

        pkt = struct.pack(PACKET_FMT, MAGIC, VERSION, 0, seq & 0xFFFFFFFF, t,
                          bass, mid, treb, bass_att, mid_att, treb_att, vol,
                          *wave, sub, sub_att)
        sock.sendto(pkt, (args.host, args.port))
        seq += 1

        if now - last_print >= 1.0:
            print(f"t={t:7.1f}s seq={seq:6d}  bass={bass:5.2f}/{bass_att:4.2f} "
                  f"mid={mid:5.2f}/{mid_att:4.2f} treb={treb:5.2f}/{treb_att:4.2f} "
                  f"vol={vol:4.2f}")
            last_print = now


if __name__ == "__main__":
    main()
