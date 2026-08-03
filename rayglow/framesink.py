"""Receive rendered frames over UDP and page-flip them onto the DPI output.

The Pi half of remote render (REMOTE-RENDER-PLAN.md): a socket and a memcpy.
No GL, no EGL, no shader stack — the GPU lives on the render host now.
Fragments arrive per rayglow/link.py, reassemble latest-wins (a lost fragment
costs that one frame, never a stall — the FPGA re-scans the last good one),
and each completed frame goes through drm_out's double-buffered hardware page
flip, the same proven sink `--output kms` uses.  After every flip one credit
datagram returns to the renderer, so the DPI vblank paces the whole remote
pipeline (plan §5.5: the flip event is the master clock; queueing is bounded
at `window` frames by construction).

Run on the Pi (DRM master on the DPI CRTC — root, same as wall runs):

    sudo ~/venv/bin/python -m rayglow.framesink

Flags: --port (default config.FRAME_PORT), --window N (frames in flight the
renderer may hold, default 2 — advertised in every credit), --backend null
(no hardware: a pacing stub, used by tools/link_check.py and desktop tests),
--pace HZ (the null backend's pretend refresh rate).

The stats line follows the renderer's convention: fps, then where the time
went, then the loss counters (all zero on a healthy same-switch link).
"""
import argparse
import select
import socket
import sys
import time

import numpy as np

from . import link, userconf
from .feed import config


class NullOut:
    """Pacing stub for --backend null: pretends to be a fixed-rate display so
    the link + credit loop runs with no DRM hardware.  Mimics drm_out's sink
    surface (blit/close/desc/acc_wait/acc_write/missed)."""

    def __init__(self, hz=122.14):
        self.period = 1.0 / hz
        self._next = None
        self.acc_wait = self.acc_write = 0.0
        self.missed = 0
        self.last = None             # last blitted frame (tests checksum it)
        self.w = self.h = None       # no geometry opinion
        self.desc = f"null sink @ {hz:g} Hz"

    def blit(self, rgb):
        self.last = rgb
        now = time.perf_counter()
        if self._next is None:
            self._next = now
        wait = self._next - now
        if wait > 0:
            time.sleep(wait)
            self.acc_wait += wait
        else:
            self.missed += int(-wait / self.period)
        self._next = max(self._next + self.period, now)

    def close(self):
        pass


def serve(sock, out, window=2, stats_every=5.0, stop=None, on_frame=None,
          quiet=False):
    """The sink loop: drain datagrams, blit the newest complete frame, credit.

    Runs until KeyboardInterrupt (or `stop`, an Event — how link_check embeds
    this in-process).  `on_frame(seq, w, h, buf)` is a test hook.  The drain
    is greedy: everything queued in the socket buffer is consumed before
    blitting, so when the renderer runs ahead the sink displays the newest
    frame and counts the bypassed ones as skipped rather than falling behind
    — latest-wins at display, not just at reassembly.
    """
    reasm = link.Reassembler()
    shown = skipped = 0
    t0 = time.perf_counter()
    fps_frames, fps_t = 0, t0
    sock.setblocking(False)
    while stop is None or not stop.is_set():
        r, _, _ = select.select([sock], [], [], 0.25)
        if not r:
            continue
        newest = None                    # (seq, w, h, buf), addr
        while True:
            try:
                dgram, addr = sock.recvfrom(65536)
            except (BlockingIOError, InterruptedError):
                break
            done = reasm.feed(dgram)
            if done:
                if newest:
                    skipped += 1
                newest = done, addr
        if not newest:
            continue
        (seq, w, h, buf), addr = newest
        if out.w is not None and (w > out.w or h > out.h):
            if not quiet:
                print(f"frame {w}x{h} exceeds display {out.w}x{out.h} — "
                      "check the renderer's --width/--height", file=sys.stderr)
            continue
        out.blit(np.frombuffer(buf, np.uint8).reshape(h, w, 3))
        shown += 1
        fps_frames += 1
        # skipped/dropped are FRAME counts (they feed the sender's token
        # replenishment — reasm.stale is per-fragment and stays out).
        sock.sendto(link.pack_credit(seq, shown, skipped, reasm.dropped,
                                     window), addr)
        if on_frame is not None:
            on_frame(seq, w, h, buf)

        now = time.perf_counter()
        if not quiet and now - fps_t >= stats_every:
            n = fps_frames
            print(f"{n / (now - fps_t):6.1f} "
                  f"{out.acc_wait / n * 1e3:6.1f}ms "
                  f"{out.acc_write / n * 1e3:6.2f}ms "
                  f"{out.missed:6d} "
                  f"{skipped:5d} {reasm.dropped:5d} {reasm.stale:6d} "
                  f"{reasm.bad + reasm.dup:4d}")
            fps_frames, fps_t = 0, now
            out.acc_wait = out.acc_write = 0.0


def main():
    ap = argparse.ArgumentParser(
        prog="framesink", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="0.0.0.0", help="bind address")
    ap.add_argument("--port", type=int, default=config.FRAME_PORT,
                    help=f"UDP port (default {config.FRAME_PORT})")
    ap.add_argument("--window", type=int, default=2,
                    help="frames in flight the renderer may hold (advertised "
                         "in every credit; default 2 — one frame of network "
                         "slack. Try 1 once stable for minimum latency)")
    ap.add_argument("--backend", choices=("drm", "null"), default="drm",
                    help="'drm' (default): page-flip the DPI CRTC via "
                         "render/drm_out. 'null': no hardware, just pace")
    ap.add_argument("--pace", type=float, default=122.14,
                    help="null backend refresh rate in Hz (default 122.14, "
                         "the production DPI mode)")
    ap.add_argument("--rcvbuf", type=int, default=4 << 20,
                    help="requested SO_RCVBUF in bytes (default 4 MiB; the "
                         "kernel clamps to net.core.rmem_max — raise that "
                         "sysctl if the effective value prints smaller)")
    # Per-machine defaults from rayglow.toml / ~/.config/rayglow/config.toml
    # ([framesink] table) — explicit flags still win.
    conf_path, conf_vals = userconf.apply(ap, "framesink")
    args = ap.parse_args()
    if conf_vals:
        print(userconf.describe(conf_path, "framesink", conf_vals))

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, args.rcvbuf)
    sock.bind((args.host, args.port))
    effective = sock.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)
    if args.backend == "drm":
        from .render.drm_out import DrmOut     # ctypes/ioctl only — no GL
        out = DrmOut()
    else:
        out = NullOut(args.pace)
    print(f"sink: {out.desc}")
    print(f"listening on UDP {args.host}:{args.port}  window {args.window}  "
          f"rcvbuf {effective // 1024} KiB"
          + ("  (kernel clamped — raise net.core.rmem_max)"
             if effective < args.rcvbuf else ""))
    print("\n   fps    wait   write  missed  skip  drop  stale  bad")
    try:
        serve(sock, out, window=args.window)
    except KeyboardInterrupt:
        pass
    finally:
        out.close()
        sock.close()


if __name__ == "__main__":
    main()
