"""Nonblocking latest-wins UDP receiver for feature packets.

Packet layout (project-milk-pi.md §5, DRAFT v0, little-endian, 556 bytes):

    offset  type         field
    0       uint32       magic = 0x4D494C4B ("MILK")
    4       uint16       version = 0
    6       uint16       flags (reserved)
    8       uint32       seq          (wraps; stale/reordered packets dropped)
    12      float32      t            (sender monotonic seconds)
    16      float32      bass         (imm_rel: normalized by running average)
    20      float32      mid
    24      float32      treb
    28      float32      bass_att     (avg_rel: smoothed)
    32      float32      mid_att
    36      float32      treb_att
    40      float32      vol          (overall, normalized)
    44      float32[128] wave         (mono waveform window, ±1.0)
    556     total (v0)

v1 appends a true sub-bass band (MilkDrop's "bass" is 0-4kHz with the low
bins equalized away — see sender.py):

    556     float32      sub          (23-117 Hz, imm_rel)
    560     float32      sub_att      (smoothed)
    564     total (v1)

Both versions are accepted; v0 packets report sub = bass.
"""
import socket
import struct

import numpy as np

from . import config

PACKET_FMT = "<IHHIf7f128f"
PACKET_FMT_V1 = "<IHHIf7f128f2f"
PACKET_SIZE = struct.calcsize(PACKET_FMT)
PACKET_SIZE_V1 = struct.calcsize(PACKET_FMT_V1)
assert PACKET_SIZE == 556, f"packet struct is {PACKET_SIZE} bytes, spec says 556"
assert PACKET_SIZE_V1 == 564

MAGIC = 0x4D494C4B
VERSIONS = {0: (PACKET_SIZE, PACKET_FMT), 1: (PACKET_SIZE_V1, PACKET_FMT_V1)}


def _seq_newer(a, b):
    """True if seq a is newer than b under uint32 wraparound (RFC1982-style)."""
    return ((a - b) & 0xFFFFFFFF) < 0x80000000 and a != b


class Receiver:
    """Bind once; call poll() every frame.  Never blocks the render loop."""

    def __init__(self, host=config.UDP_HOST, port=config.UDP_PORT):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((host, port))
        self.sock.setblocking(False)
        self._last_seq = None

    def poll(self):
        """Drain the socket, return the newest valid packet as a dict, or None.

        Keeps only the highest-seq packet seen this drain; drops anything
        not newer than the last packet handed out (reordered/stale).
        """
        best = None
        best_seq = None
        while True:
            try:
                data, _addr = self.sock.recvfrom(2048)
            except (BlockingIOError, InterruptedError):
                break
            if len(data) < PACKET_SIZE:
                continue
            version = struct.unpack_from("<H", data, 4)[0]
            if version not in VERSIONS:
                continue
            size, fmt = VERSIONS[version]
            if len(data) != size:
                continue
            fields = struct.unpack(fmt, data)
            if fields[0] != MAGIC:
                continue
            seq = fields[3]
            if best is None or _seq_newer(seq, best_seq):
                best = fields
                best_seq = seq

        if best is None:
            return None
        if self._last_seq is not None and not _seq_newer(best_seq, self._last_seq):
            return None  # stale vs. what we already rendered with
        self._last_seq = best_seq

        return {
            "seq": best_seq,
            "t": best[4],
            "bass": best[5],
            "mid": best[6],
            "treb": best[7],
            "bass_att": best[8],
            "mid_att": best[9],
            "treb_att": best[10],
            "vol": best[11],
            "wave": np.asarray(best[12:140], dtype=np.float32),
            # v1: true sub-bass; v0 senders don't have it — fall back to bass
            "sub": best[140] if len(best) > 140 else best[5],
            "sub_att": best[141] if len(best) > 141 else best[8],
        }

    def close(self):
        self.sock.close()
