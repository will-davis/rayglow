"""Direct DRM/KMS output: double-buffered dumb framebuffers + atomic page flips.

Why this exists: /dev/fb0 on the Pi's DPI is the kernel's fbdev EMULATION — a shadow
buffer whose contents a kernel worker copies to the real scanout buffer in page-sized
chunks (4 KB ≈ 2.7 rows) at its own cadence. No amount of vblank-paced writing into the
shadow prevents the copy from racing the beam: the wall showed 2-3-row shear bands under
fast motion. This backend bypasses fbdev entirely: it becomes DRM master, allocates two
dumb buffers in scanout memory, points the CRTC at one, renders into the other, and asks
the hardware to PAGE FLIP at vblank. Tearing is impossible by construction, and the
flip-complete event paces the render loop with a full frame of slack.

Pure ctypes/ioctl — no libdrm dependency. Legacy (non-atomic) KMS API: universally
supported, and we drive exactly one CRTC/connector. The console (fbcon) keeps writing
to its shadow fb, which is no longer scanned out — so the blinking cursor stops
photobombing the wall; it all comes back on VT switch / process exit releasing master.
"""
import fcntl
import mmap
import os
import struct

import numpy as np

_IOCTL_SET_MASTER = 0x641E
_IOCTL_DROP_MASTER = 0x641F
_IOCTL_MODE_GETRESOURCES = 0xC04064A0
_IOCTL_MODE_GETCONNECTOR = 0xC05064A7
_IOCTL_MODE_GETENCODER = 0xC01464A6
_IOCTL_MODE_ADDFB = 0xC01C64AE
_IOCTL_MODE_SETCRTC = 0xC06864A2
_IOCTL_MODE_PAGE_FLIP = 0xC01864B0
_IOCTL_MODE_CREATE_DUMB = 0xC02064B2
_IOCTL_MODE_MAP_DUMB = 0xC01064B3

_PAGE_FLIP_EVENT = 0x01
_EVENT_FLIP_COMPLETE = 0x02
_CONNECTED = 1
_MODEINFO = 68          # sizeof(struct drm_mode_modeinfo)


def _u64ptr(arr):
    return arr.ctypes.data if len(arr) else 0


class DrmOut:
    """Own a CRTC: blit (H, W, 3) RGB into the back dumb buffer, flip, wait the event."""

    def __init__(self, card=None):
        self.card = card or self._find_card()
        self.fd = os.open(self.card, os.O_RDWR)
        try:
            fcntl.ioctl(self.fd, _IOCTL_SET_MASTER)
        except OSError as e:
            os.close(self.fd)
            raise OSError(f"{self.card}: cannot become DRM master ({e}) — is a "
                          "compositor running?")
        conn_id, self.mode = self._pick_connector()
        self.crtc_id = self._pick_crtc()
        self.w = struct.unpack_from("H", self.mode, 4)[0]    # hdisplay
        self.h = struct.unpack_from("H", self.mode, 14)[0]   # vdisplay
        self.fbs = [self._make_fb(self.w, self.h) for _ in range(2)]
        self.back = 1

        # Point the CRTC at fb[0] with the connector's current mode.
        # struct drm_mode_crtc: set_connectors_ptr u64; count_connectors, crtc_id, fb_id,
        # x, y, gamma_size, mode_valid (u32 each); struct drm_mode_modeinfo mode (68 B).
        conns = np.array([conn_id], dtype=np.uint32)
        req = struct.pack("QIIIIIII", conns.ctypes.data, 1, self.crtc_id,
                          self.fbs[0]["fb_id"], 0, 0, 0, 1) + self.mode
        fcntl.ioctl(self.fd, _IOCTL_MODE_SETCRTC, bytearray(req))

        self.acc_wait = self.acc_write = 0.0
        self.missed = 0
        self._last_seq = None
        self._flip_pending = False
        self.desc = (f"DRM page-flip {self.card} crtc {self.crtc_id} "
                     f"{self.w}x{self.h} XR24 double-buffered")

    @staticmethod
    def _find_card():
        import glob
        for card in sorted(glob.glob("/dev/dri/card[0-9]*")):
            name = os.path.basename(card)
            for conn in glob.glob(f"/sys/class/drm/{name}-*/status"):
                try:
                    if open(conn).read().strip() == "connected":
                        return card
                except OSError:
                    pass
        raise OSError("no DRM card with a connected connector")

    def _ioctl(self, num, buf):
        fcntl.ioctl(self.fd, num, buf)
        return buf

    def _pick_connector(self):
        # GETRESOURCES twice: counts, then arrays.
        res = self._ioctl(_IOCTL_MODE_GETRESOURCES, bytearray(64))
        counts = struct.unpack_from("IIII", res, 32)
        fbs = np.zeros(counts[0], np.uint32)
        crtcs = np.zeros(counts[1], np.uint32)
        conns = np.zeros(counts[2], np.uint32)
        encs = np.zeros(counts[3], np.uint32)
        req = bytearray(struct.pack("QQQQIIIIIIII", _u64ptr(fbs), _u64ptr(crtcs),
                                    _u64ptr(conns), _u64ptr(encs), *counts, 0, 0, 0, 0))
        self._ioctl(_IOCTL_MODE_GETRESOURCES, req)
        self._crtcs = crtcs
        for cid in conns:
            got = self._get_connector(int(cid))
            if got is not None:
                return got
        raise OSError("no connected DRM connector with modes")

    def _get_connector(self, cid):
        hdr = bytearray(80)
        struct.pack_into("I", hdr, 48, cid)          # connector_id at offset 48
        self._ioctl(_IOCTL_MODE_GETCONNECTOR, hdr)
        # offsets: 32 count_modes, 36 count_props, 40 count_encoders, 44 encoder_id,
        # 48 connector_id, 52 connector_type, 56 connector_type_id, 60 connection
        count_modes = struct.unpack_from("I", hdr, 32)[0]
        connection = struct.unpack_from("I", hdr, 60)[0]
        encoder_id = struct.unpack_from("I", hdr, 44)[0]
        if connection != _CONNECTED or count_modes == 0:
            return None
        modes = bytearray(count_modes * _MODEINFO)
        arr = (np.frombuffer(modes, np.uint8))       # keep a stable buffer address
        req = bytearray(80)
        struct.pack_into("Q", req, 8, arr.ctypes.data)   # modes_ptr
        struct.pack_into("I", req, 32, count_modes)
        struct.pack_into("I", req, 48, cid)
        self._ioctl(_IOCTL_MODE_GETCONNECTOR, req)
        self._encoder_id = encoder_id
        return cid, bytes(modes[:_MODEINFO])         # current/preferred mode

    def _pick_crtc(self):
        if getattr(self, "_encoder_id", 0):
            enc = bytearray(20)
            struct.pack_into("I", enc, 0, self._encoder_id)
            self._ioctl(_IOCTL_MODE_GETENCODER, enc)
            crtc = struct.unpack_from("I", enc, 8)[0]
            if crtc:
                return crtc
        if len(self._crtcs):
            return int(self._crtcs[0])
        raise OSError("no CRTC available")

    def _make_fb(self, w, h):
        creq = bytearray(struct.pack("IIIIIIQ", h, w, 32, 0, 0, 0, 0))
        self._ioctl(_IOCTL_MODE_CREATE_DUMB, creq)
        _, _, _, _, handle, pitch, size = struct.unpack("IIIIIIQ", creq)
        freq = bytearray(struct.pack("IIIIIII", 0, w, h, pitch, 32, 24, handle))
        self._ioctl(_IOCTL_MODE_ADDFB, freq)
        fb_id = struct.unpack_from("I", freq, 0)[0]
        mreq = bytearray(struct.pack("IIQ", handle, 0, 0))
        self._ioctl(_IOCTL_MODE_MAP_DUMB, mreq)
        offset = struct.unpack_from("Q", mreq, 8)[0]
        mm = mmap.mmap(self.fd, size, offset=offset)
        return {"fb_id": fb_id, "pitch": pitch, "mm": mm, "size": size}

    def _to_fb(self, rgb, pitch):
        h, w = rgb.shape[:2]
        out = np.zeros((h, pitch // 4, 4), np.uint8)  # XR24: B,G,R,X little-endian
        out[:, :w, 0] = rgb[..., 2]
        out[:, :w, 1] = rgb[..., 1]
        out[:, :w, 2] = rgb[..., 0]
        return out

    def _wait_flip(self):
        while self._flip_pending:
            data = os.read(self.fd, 1024)
            off = 0
            while off + 8 <= len(data):
                etype, elen = struct.unpack_from("II", data, off)
                if etype == _EVENT_FLIP_COMPLETE:
                    seq = struct.unpack_from("I", data, off + 24)[0]
                    if self._last_seq is not None:
                        self.missed += max(0, seq - self._last_seq - 1)
                    self._last_seq = seq
                    self._flip_pending = False
                off += max(elen, 8)

    def blit(self, rgb):
        """Write into the back buffer, schedule a flip, wait for it to complete."""
        import time
        fb = self.fbs[self.back]
        h = min(rgb.shape[0], self.h)
        t0 = time.perf_counter()
        fb["mm"][0:h * fb["pitch"]] = self._to_fb(
            np.ascontiguousarray(rgb[:h]), fb["pitch"]).tobytes()
        t1 = time.perf_counter()
        req = struct.pack("IIIIQ", self.crtc_id, fb["fb_id"], _PAGE_FLIP_EVENT, 0, 0)
        fcntl.ioctl(self.fd, _IOCTL_MODE_PAGE_FLIP, req)
        self._flip_pending = True
        self.back ^= 1
        self._wait_flip()                    # returns at vblank; full frame of slack
        t2 = time.perf_counter()
        self.acc_write += t1 - t0
        self.acc_wait += t2 - t1

    def close(self):
        try:
            self._wait_flip()
        except OSError:
            pass
        for fb in self.fbs:
            fb["mm"].close()
        try:
            fcntl.ioctl(self.fd, _IOCTL_DROP_MASTER)
        finally:
            os.close(self.fd)
