#!/usr/bin/env python3
"""Milk-Pi desktop feature daemon — the broadcast half of the LED-panel visualizer.

Captures the PipeWire monitor of the default sink (whatever the desktop is
playing), extracts per-frame audio features, and sends 564-byte v1 packets
over unicast UDP at ~60 Hz to the Pi, which renders on a 256x32 HUB75 matrix.

Receiving end (Pi 4B at 192.168.2.108, ~/rpi-rgb-led-matrix/will-rpi-custom/,
mounted on this desktop at ~/local-mount/rpi4/):
  - milk/receiver.py — the other half of the packet contract (accepts v0+v1,
    nonblocking latest-wins drain)
  - milk/features.py — FeatureState: latest packet values + synth fallback
  - shadertoy/ — THE renderer: Shadertoy-compatible GLSL on the Pi's
    VideoCore VI GPU (headless EGL + GLES3).  This packet's features enter
    shaders as iChannel textures: 'milk' (8x1 float — bands + Pi-derived
    signals; texel map in shadertoy/textures.py MilkChannel, live reference
    card in shadertoy/presets/milk-verbose.glsl) and 'audio' (512x2
    Shadertoy-style spectrum/waveform rebuilt from this packet's wave[128]).
  - milk/ — the original MilkDrop-faithful NumPy/OpenCV renderer (this
    project's first life; still runnable).  milk/fake_sender.py is the
    music-free test harness speaking the same struct.

The analysis chain is a faithful port of MilkDrop3's (code/ in the MilkDrop3
repo).  MilkDrop is no longer the renderer, but its auto-gain semantics —
each band normalized by its own running average, 1.0 = "typical for this
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

v1 extension (NOT MilkDrop): a true sub-bass band.  MilkDrop's "bass" is
linear bins 0..85 = 0-4kHz with a log-equalize that suppresses the lowest
bins ~90x — subwoofer content is effectively invisible in it.  `sub` fixes
that: 2048-sample FFT (23.4 Hz/bin), raw magnitudes (no equalize), bins
1..5 = 23-117 Hz, own AutoGain.  Appended to the packet as (sub, sub_att).
In shaders it's milk-texture texel 4 (or band index 4 anywhere bands are
ordered bass/mid/treb/vol/sub).

Packet layout: PACKET_FMT below, mirrored in milk/receiver.py; full field
table in README.md.  (project-milk-pi.md §5 documents the v0 ancestor and
the retired milk renderer — kept as the historical record.)

Run:  uv run sender.py [--host 192.168.2.108] [--port 5005] [--source NAME]
      uv run sender.py --list-sources
      uv run sender.py --debug          # adds raw (pre-normalization) band prints
"""
import argparse
import os
import socket
import struct
import subprocess
import sys
import time

import numpy as np

# ---- packet v1 — contract mirrored in milk/receiver.py & fake_sender.py ------
# v1 = the v0 layout (project-milk-pi.md §5) with (sub, sub_att) appended;
# the receiver accepts both and substitutes sub=bass for v0 senders.
PACKET_FMT = "<IHHIf7f128f2f"   # magic, ver, flags, seq, t, 6 bands + vol, wave[128], sub, sub_att
MAGIC = 0x4D494C4B              # "MILK"
VERSION = 1
WAVE_SAMPLES = 128
assert struct.calcsize(PACKET_FMT) == 564

# ---- defaults ----------------------------------------------------------------
HOST = "192.168.2.108"          # the Pi (IoT VLAN)
PORT = 5005
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
    gains = [AutoGain() for _ in range(5)]      # bass, mid, treb, vol, sub
    wave_x = np.linspace(0.0, WINDOW - 1, WAVE_SAMPLES)

    print(f"sender: {source} -> {args.host}:{args.port} @ {args.fps:.0f} Hz (ctrl-c to stop)")

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

        frames = cap.latest(WINDOW)                       # (576, 2)
        raw = analyze(frames[:, 0])                       # left channel, like MilkDrop
        rels = [g.update(imm, args.fps) for g, imm in zip(gains, raw)]
        (bass, bass_att), (mid, mid_att), (treb, treb_att), (vol, _) = rels

        raw_sub = analyze_sub(cap.latest(SUB_WINDOW)[:, 0])
        sub, sub_att = gains[4].update(raw_sub, args.fps)

        mono = frames.mean(axis=1)                        # mono mix for the drawn wave
        wave = np.clip(np.interp(wave_x, np.arange(WINDOW), mono),
                       -1.0, 1.0).astype(np.float32)

        pkt = struct.pack(PACKET_FMT, MAGIC, VERSION, 0, seq & 0xFFFFFFFF, t,
                          bass, mid, treb, bass_att, mid_att, treb_att, vol,
                          *wave, sub, sub_att)
        sock.sendto(pkt, (args.host, args.port))
        seq += 1

        if now - last_print >= 1.0:
            line = (f"t={t:7.1f}s seq={seq:6d}  sub={sub:5.2f}/{sub_att:4.2f} "
                    f"bass={bass:5.2f}/{bass_att:4.2f} "
                    f"mid={mid:5.2f}/{mid_att:4.2f} treb={treb:5.2f}/{treb_att:4.2f} "
                    f"vol={vol:4.2f}")
            if args.debug:
                line += (f"  raw=({raw[0]:6.3f} {raw[1]:6.3f} {raw[2]:6.3f} "
                         f"sub={raw_sub:6.3f})")
            print(line)
            last_print = now


if __name__ == "__main__":
    main()
