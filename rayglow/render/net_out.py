"""UDP frame sink for --output net: ship resolved RGB frames to the Pi's framesink.

The render-host half of remote render (REMOTE-RENDER-PLAN.md): each frame is
fragmented per rayglow/link.py and sent to the framesink, which credits back
one datagram per DPI page flip.  send() blocks on those credits — that is the
whole point: the Pi's flip event paces this loop exactly like drm_out's
flip-complete event paces a local --output kms run, so the renderer produces
precisely the rate the wall consumes and end-to-end latency is bounded at
window x flip-period.  Free-running (the plan's §5.5 bufferbloat trap) is
impossible by construction.

Credits carry absolute counters, so a lost credit heals on the next one; if
the sink goes silent past --net-timeout (restart, cable pull) the ledger
resets and the loop degrades to a slow window-per-timeout probe instead of
deadlocking, then snaps back when credits resume.

The socket is connect()ed: sends skip per-packet routing, and only the
sink's replies come back on recv.  sendmsg([header, payload]) scatter-gathers
straight out of the frame's memoryview — no per-fragment copies.
"""
import socket
import sys
import time

import numpy as np

from .. import link
from ..feed import config

_IP_UDP_OVERHEAD = 28            # IPv4 20 + UDP 8


class NetOut:
    """blit (H, W, 3) RGB frames over UDP, paced by the sink's flip credits."""

    def __init__(self, host, port=config.FRAME_PORT, window=2, mtu=1500,
                 timeout=1.0):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.connect((host, port))
        try:                        # best effort; a full window is ~300 KB
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1 << 20)
        except OSError:
            pass
        self.chunk = max(512, mtu - _IP_UDP_OVERHEAD - link.FRAG_HDR)
        self.timeout = timeout
        self.ledger = link.CreditLedger(window)
        self._sent_t = {}           # seq -> perf_counter at send (age probe)
        self.acc_wait = 0.0         # blocked on credits (the pacing)
        self.acc_send = 0.0         # fragment + sendmsg time
        self.acc_age = 0.0          # send->displayed round trip, credited seqs
        self.n_age = 0
        self.stalls = 0             # timeout resets (sink silent)
        self.desc = (f"net {host}:{port} chunk {self.chunk}B "
                     f"window {self.ledger.window} timeout {timeout:g}s")

    def _note(self, dgram, now):
        cred = link.unpack_credit(dgram)
        self.ledger.note_credit(cred)
        if cred:
            t = self._sent_t.pop(cred["seq_shown"], None)
            if t is not None:
                self.acc_age += now - t
                self.n_age += 1

    def _drain_credits(self):
        self.sock.settimeout(0)
        while True:
            try:
                dgram = self.sock.recv(2048)
            except (BlockingIOError, InterruptedError):
                return
            except ConnectionRefusedError:
                return              # ICMP port-unreachable: sink not up (yet)
            self._note(dgram, time.perf_counter())

    def blit(self, rgb):
        """Wait for credit, then fragment + send one frame. Blocking here IS
        the frame pacing (the sink's page flip is the master clock)."""
        t0 = time.perf_counter()
        self._drain_credits()
        deadline = t0 + self.timeout
        while not self.ledger.can_send():
            remain = deadline - time.perf_counter()
            if remain <= 0:
                self.stalls += 1
                if self.stalls <= 3 or self.stalls % 60 == 0:
                    print(f"net: no credits for {self.timeout:g}s "
                          f"(framesink down?) — probing on", file=sys.stderr)
                self.ledger.reset()
                break
            self.sock.settimeout(remain)
            try:
                dgram = self.sock.recv(2048)
            except socket.timeout:
                continue
            except ConnectionRefusedError:
                continue
            self._note(dgram, time.perf_counter())
        t1 = time.perf_counter()

        h, w = rgb.shape[:2]
        frame = np.ascontiguousarray(rgb)
        seq = self.ledger.take_seq()
        self._sent_t[seq] = t1
        if len(self._sent_t) > 64:  # skipped seqs never get an age credit
            for s in sorted(self._sent_t)[:32]:
                del self._sent_t[s]
        self.sock.settimeout(None)
        try:
            for hdr, payload in link.fragment(frame, w, h, seq, self.chunk):
                self.sock.sendmsg([hdr, payload])
        except ConnectionRefusedError:
            pass    # ICMP port-unreachable: sink down — this frame is lost,
        t2 = time.perf_counter()   # which is what latest-wins means
        self.acc_wait += t1 - t0
        self.acc_send += t2 - t1

    def stats_reset(self):
        self.acc_wait = self.acc_send = self.acc_age = 0.0
        self.n_age = 0

    def close(self):
        self.sock.close()
