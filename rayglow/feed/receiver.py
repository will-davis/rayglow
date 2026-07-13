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
(bit 5) in v2+; it is always 0 for v0/v1.

v3 (2996 bytes) keeps the v2 header/legacy prefix byte-for-byte (the legacy
bands are still computed by the sender's unchanged MilkDrop path) and rebuilds
the rest around 8 log-spaced bands with flywheel envelopes and theta phase
accumulators (all derived on the sender's steady clock now — the Pi no longer
integrates), a predictive beat tracker, a 128-bin spectrum, and key detection:

    16      float32[7]   bass..vol    (legacy, as v0/v1/v2)
    44      float32[2]   sub, sub_att (legacy)
    52      float32[8]   band_imm     (8 log bands 20Hz..16kHz, AGC'd)
    84      float32[24]  band_env     ([band][tier], band-major; 3 flywheel
                                       tiers: ~125ms sym, punchy, heavy)
    180     float32[24]  band_theta   ([band][tier], mod 200*pi; integrates
                                       imm / env1 / env2)
    276     float32[8]   band_onset   (per-band rectified flux, AGC'd)
    308     float32[7]   vol_imm, vol_env[3], vol_theta[3]
    336     float32[512] wave         (as v2)
    2384    float32[128] spec         (hybrid lin/log axis, recomputed for 128)
    2896    float32[12]  chroma       (as v2)
    2944    float32[5]   centroid, flux, flatness, rolloff, crest
    2964    float32[4]   bpm, beat_phase (predictive 0->1 ramp), bar_phase,
                         beat_conf
    2980    float32[2]   width, pan
    2988    float32[2]   key_idx (0-11 C..B major, 12-23 minor), key_conf
    2996    total (v3)

All versions are accepted (the receiver dispatches on version + exact byte
length).  Older senders report sub = bass and zeros/defaults for fields their
version doesn't carry.
"""
import socket
import struct

import numpy as np

from . import config

PACKET_FMT = "<IHHIf7f128f"
PACKET_FMT_V1 = "<IHHIf7f128f2f"
PACKET_FMT_V2 = "<IHHIf7f2f512f512f12f5f3f2f"
PACKET_FMT_V3 = "<IHHIf7f2f8f24f24f8f7f512f128f12f5f4f2f2f"
PACKET_SIZE = struct.calcsize(PACKET_FMT)
PACKET_SIZE_V1 = struct.calcsize(PACKET_FMT_V1)
PACKET_SIZE_V2 = struct.calcsize(PACKET_FMT_V2)
PACKET_SIZE_V3 = struct.calcsize(PACKET_FMT_V3)
assert PACKET_SIZE == 556, f"packet struct is {PACKET_SIZE} bytes, spec says 556"
assert PACKET_SIZE_V1 == 564
assert PACKET_SIZE_V2 == 4236
assert PACKET_SIZE_V3 == 2996

MAGIC = 0x4D494C4B
VERSIONS = {0: (PACKET_SIZE, PACKET_FMT),
            1: (PACKET_SIZE_V1, PACKET_FMT_V1),
            2: (PACKET_SIZE_V2, PACKET_FMT_V2),
            3: (PACKET_SIZE_V3, PACKET_FMT_V3)}

# v2 tuple field offsets (header is 5 values: magic, ver, flags, seq, t).
_V2_WAVE = slice(14, 14 + 512)
_V2_SPEC = slice(_V2_WAVE.stop, _V2_WAVE.stop + 512)
_V2_CHROMA = slice(_V2_SPEC.stop, _V2_SPEC.stop + 12)
_V2_DESC = _V2_CHROMA.stop                       # centroid..crest (5)
_V2_BEAT = _V2_DESC + 5                           # bpm, beat_phase, beat_conf
_V2_STEREO = _V2_BEAT + 3                         # width, pan

# v3 tuple field offsets (same 5-value header; f[5..13] = legacy bands + sub).
_V3_BANDS = slice(14, 14 + 8)                     # band_imm[8]
_V3_BENV = slice(_V3_BANDS.stop, _V3_BANDS.stop + 24)     # [band][tier]
_V3_BTHETA = slice(_V3_BENV.stop, _V3_BENV.stop + 24)     # [band][tier]
_V3_BONSET = slice(_V3_BTHETA.stop, _V3_BTHETA.stop + 8)  # band_onset[8]
_V3_VOL = _V3_BONSET.stop                         # vol_imm + env[3] + theta[3]
_V3_WAVE = slice(_V3_VOL + 7, _V3_VOL + 7 + 512)
_V3_SPEC = slice(_V3_WAVE.stop, _V3_WAVE.stop + 128)
_V3_CHROMA = slice(_V3_SPEC.stop, _V3_SPEC.stop + 12)
_V3_DESC = _V3_CHROMA.stop                        # centroid..crest (5)
_V3_BEAT = _V3_DESC + 5                           # bpm, beat_phase, bar, conf
_V3_STEREO = _V3_BEAT + 4                         # width, pan
_V3_KEY = _V3_STEREO + 2                          # key_idx, key_conf


def _seq_newer(a, b):
    """True if seq a is newer than b under uint32 wraparound (RFC1982-style)."""
    return ((a - b) & 0xFFFFFFFF) < 0x80000000 and a != b


def _to_dict(f, seq):
    """Unpacked packet tuple -> feature dict, version-aware.

    Common fields (header, legacy bands, the flags bits) are shared; each
    version's extra groups appear only when that version carries them.
    Older senders get sub = bass, None for arrays they don't send
    (FeatureState keeps its defaults) and simply omit newer scalars
    (FeatureState holds its last value) — so a v1/v2 sender drives a v3
    receiver with the newer channels idle, never garbage.
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
    if f[1] >= 3:
        out["sub"] = f[12]
        out["sub_att"] = f[13]
        out["bands"] = np.asarray(f[_V3_BANDS], dtype=np.float32)
        out["band_env"] = np.asarray(f[_V3_BENV], dtype=np.float32).reshape(8, 3)
        out["band_theta"] = np.asarray(f[_V3_BTHETA],
                                       dtype=np.float32).reshape(8, 3)
        out["band_onset"] = np.asarray(f[_V3_BONSET], dtype=np.float32)
        out["vol_imm"] = f[_V3_VOL]
        out["vol_env"] = np.asarray(f[_V3_VOL + 1:_V3_VOL + 4], dtype=np.float32)
        out["vol_theta"] = np.asarray(f[_V3_VOL + 4:_V3_VOL + 7],
                                      dtype=np.float32)
        out["wave"] = np.asarray(f[_V3_WAVE], dtype=np.float32)
        out["spec"] = np.asarray(f[_V3_SPEC], dtype=np.float32)
        out["chroma"] = np.asarray(f[_V3_CHROMA], dtype=np.float32)
        out["centroid"], out["flux"], out["flatness"], out["rolloff"], \
            out["crest"] = f[_V3_DESC:_V3_DESC + 5]
        out["bpm"], out["beat_phase"], out["bar_phase"], out["beat_conf"] = \
            f[_V3_BEAT:_V3_BEAT + 4]
        out["width"], out["pan"] = f[_V3_STEREO:_V3_STEREO + 2]
        out["key_idx"], out["key_conf"] = f[_V3_KEY:_V3_KEY + 2]
    elif f[1] == 2:
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
