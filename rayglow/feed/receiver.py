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

v2 is a richer feed (4236 bytes).  The header and the 9 band floats keep
their v1 semantics, but the field ORDER changes (sub moves ahead of wave) and
five new groups are appended — so v2 is parsed against its own layout, not as
a v1 extension:

    16      float32[7]   bass..vol    (as v0/v1)
    44      float32      sub
    48      float32      sub_att
    52      float32[512] wave         (now 512 samples, ±1.0)
    2100    float32[512] spec         (hybrid lin/log spectrum 30Hz..16kHz, dB-norm 0..1)
    4148    float32[12]  chroma       (pitch classes C..B, peak-normalized)
    4196    float32[5]   centroid, flux, flatness, rolloff, crest
    4216    float32[3]   bpm, beat_phase, beat_conf
    4228    float32[2]   width, pan
    4236    total (v2)

The `flags` u16 carries source_domain (bits 0-3) + BEAT (bit 4) + DOWNBEAT
(bit 5) in v2; it is always 0 for v0/v1.

All versions are accepted (the receiver dispatches on version + exact byte
length).  Older senders report sub = bass and zeros/defaults for the v2-only
fields.
"""
import socket
import struct

import numpy as np

from . import config

PACKET_FMT = "<IHHIf7f128f"
PACKET_FMT_V1 = "<IHHIf7f128f2f"
PACKET_FMT_V2 = "<IHHIf7f2f512f512f12f5f3f2f"
PACKET_SIZE = struct.calcsize(PACKET_FMT)
PACKET_SIZE_V1 = struct.calcsize(PACKET_FMT_V1)
PACKET_SIZE_V2 = struct.calcsize(PACKET_FMT_V2)
assert PACKET_SIZE == 556, f"packet struct is {PACKET_SIZE} bytes, spec says 556"
assert PACKET_SIZE_V1 == 564
assert PACKET_SIZE_V2 == 4236

MAGIC = 0x4D494C4B
VERSIONS = {0: (PACKET_SIZE, PACKET_FMT),
            1: (PACKET_SIZE_V1, PACKET_FMT_V1),
            2: (PACKET_SIZE_V2, PACKET_FMT_V2)}

# v2 tuple field offsets (header is 5 values: magic, ver, flags, seq, t).
_V2_WAVE = slice(14, 14 + 512)
_V2_SPEC = slice(_V2_WAVE.stop, _V2_WAVE.stop + 512)
_V2_CHROMA = slice(_V2_SPEC.stop, _V2_SPEC.stop + 12)
_V2_DESC = _V2_CHROMA.stop                       # centroid..crest (5)
_V2_BEAT = _V2_DESC + 5                           # bpm, beat_phase, beat_conf
_V2_STEREO = _V2_BEAT + 3                         # width, pan


def _seq_newer(a, b):
    """True if seq a is newer than b under uint32 wraparound (RFC1982-style)."""
    return ((a - b) & 0xFFFFFFFF) < 0x80000000 and a != b


def _to_dict(f, seq):
    """Unpacked packet tuple -> feature dict, version-aware.

    Common fields (header, bands, the flags bits) are shared; wave/sub move
    and the v2-only groups appear only for version >= 2.  Older senders get
    sub = bass and None for the v2 arrays (FeatureState keeps its defaults),
    so a v1 sender drives a v2 receiver with the spectrum channels idle.
    """
    flags = f[2]
    out = {
        "seq": seq, "t": f[4],
        "bass": f[5], "mid": f[6], "treb": f[7],
        "bass_att": f[8], "mid_att": f[9], "treb_att": f[10],
        "vol": f[11],
        "source_domain": flags & 0x0F,
        "beat": bool(flags & 0x10),
        "downbeat": bool(flags & 0x20),
    }
    if f[1] >= 2:
        out["sub"] = f[12]
        out["sub_att"] = f[13]
        out["wave"] = np.asarray(f[_V2_WAVE], dtype=np.float32)
        out["spec"] = np.asarray(f[_V2_SPEC], dtype=np.float32)
        out["chroma"] = np.asarray(f[_V2_CHROMA], dtype=np.float32)
        out["centroid"], out["flux"], out["flatness"], out["rolloff"], \
            out["crest"] = f[_V2_DESC:_V2_DESC + 5]
        out["bpm"], out["beat_phase"], out["beat_conf"] = f[_V2_BEAT:_V2_BEAT + 3]
        out["width"], out["pan"] = f[_V2_STEREO:_V2_STEREO + 2]
    else:
        out["wave"] = np.asarray(f[12:140], dtype=np.float32)
        # v1: true sub-bass; v0 senders don't have it — fall back to bass
        out["sub"] = f[140] if len(f) > 140 else f[5]
        out["sub_att"] = f[141] if len(f) > 141 else f[8]
        out["spec"] = out["chroma"] = None     # FeatureState keeps its defaults
    return out


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
                data, _addr = self.sock.recvfrom(65536)   # v2 packet ~4.2 KB
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

        return _to_dict(best, best_seq)

    def close(self):
        self.sock.close()
