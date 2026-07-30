"""KMS/DPI output sink: blit rendered frames into the Pi's DPI framebuffer.

The FPGA translation layer (~/Projects/rayglow-fpga) captures the Pi's DPI video and
does the HUB75 fold + BCM scan-out itself, so this "transport" is nothing more than
writing the rendered (H, W, 3) frame into /dev/fb0. The FPGA is, quite literally, a
monitor on the end of the Pi's display output.

Contrast with the RP2350 path (run_wall): there the host folds to chains, packs bit
planes, and ships them over the PIO/SPI link. Here none of that happens on the host —
raw display-referred RGB goes out over DPI and the FPGA owns gamma (its CIE LUT) and
geometry. See ~/Projects/rayglow-fpga/INTERFACE-CONTRACT.md.

Blit target is the top-left W×H of the framebuffer. The DPI mode is typically larger
than the rendered wall (the RP1 DPI driver clamps vactive to 480), so the rest of the
framebuffer is left black and the FPGA crops to the wall region it drives.
"""
import fcntl
import glob
import mmap
import os
import struct

import numpy as np


def _sysfs_int(path, default):
    try:
        return int(open(path).read().strip())
    except OSError:
        return default


# union drm_wait_vblank is 24 bytes (request: u32 type, u32 seq, u64 signal;
# reply: u32 type, u32 seq, s64 tval_sec, s64 tval_usec) -> _IOWR('d', 0x3a, 24).
_DRM_IOCTL_WAIT_VBLANK = 0xC018643A
_DRM_VBLANK_RELATIVE = 1


class _VBlank:
    """Wait for the display's vertical blank (DRM_IOCTL_WAIT_VBLANK).

    Blitting right after vblank starts means the write completes during blanking +
    the first few scanout lines — the scanout never reads a half-updated frame. This
    is what makes the fbdev path tear-free without full DRM page-flipping. Finds the
    DRM card backing the fbdev by matching their parent platform device in sysfs.
    """

    def __init__(self, fbdev):
        fb_parent = os.path.realpath(
            f"/sys/class/graphics/{os.path.basename(fbdev)}/device")
        self.fd = None
        for card in sorted(glob.glob("/dev/dri/card[0-9]*")):
            parent = os.path.realpath(
                f"/sys/class/drm/{os.path.basename(card)}/device")
            if parent != fb_parent:
                continue
            fd = os.open(card, os.O_RDWR)
            try:
                self._ioctl(fd)             # probe: driver must support vblank waits
            except OSError:
                os.close(fd)
                raise
            self.fd, self.card = fd, card
            return
        raise OSError(f"no /dev/dri/card* shares a parent device with {fbdev}")

    def _ioctl(self, fd):
        buf = bytearray(struct.pack("IIqq", _DRM_VBLANK_RELATIVE, 1, 0, 0))
        fcntl.ioctl(fd, _DRM_IOCTL_WAIT_VBLANK, buf)

    def wait(self):
        self._ioctl(self.fd)

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


class KmsOut:
    """Open /dev/fb0 and blit (H, W, 3) uint8 RGB frames to its top-left corner.

    vsync=True (default) paces every blit to the display's vertical blank, which
    both eliminates tearing and locks the render loop to the DPI refresh (60 Hz).
    Falls back to unpaced blits (with a note in `desc`) if the driver refuses.
    """

    def __init__(self, fbdev="/dev/fb0", vsync=True):
        base = "/sys/class/graphics/" + os.path.basename(fbdev)
        try:
            vw, vh = open(base + "/virtual_size").read().strip().split(",")
            self.fb_w, self.fb_h = int(vw), int(vh)
        except OSError:
            raise OSError(f"{fbdev}: cannot read framebuffer geometry — is the DPI "
                          f"overlay loaded and a DRM/fbdev device present?")
        self.bpp = _sysfs_int(base + "/bits_per_pixel", 32)
        self.Bpp = self.bpp // 8
        self.stride = _sysfs_int(base + "/stride", self.fb_w * self.Bpp)
        if self.Bpp not in (2, 4):
            raise OSError(f"{fbdev}: unsupported {self.bpp}-bit framebuffer "
                          "(need 16-bit RGB565 or 32-bit XRGB8888)")
        try:
            self._fd = os.open(fbdev, os.O_RDWR)
        except PermissionError:
            raise PermissionError(
                f"{fbdev}: permission denied — add the user to the 'video' group "
                "(sudo usermod -aG video $USER; re-login) or run with privilege.")
        self._mm = mmap.mmap(self._fd, self.stride * self.fb_h)
        self.desc = (f"KMS {fbdev} {self.fb_w}x{self.fb_h} {self.bpp}bpp "
                     f"stride={self.stride}")
        self._vbl = None
        if vsync:
            try:
                self._vbl = _VBlank(fbdev)
                self.desc += f" +vsync ({self._vbl.card})"
            except OSError as e:
                self.desc += f" (vsync unavailable, unpaced: {e})"

    def _to_fb(self, rgb):
        """(H, W, 3) uint8 RGB -> framebuffer-native bytes, one row at a time later."""
        h, w = rgb.shape[:2]
        if self.Bpp == 4:                    # XRGB8888: little-endian bytes B,G,R,X
            out = np.empty((h, w, 4), np.uint8)
            out[..., 0] = rgb[..., 2]
            out[..., 1] = rgb[..., 1]
            out[..., 2] = rgb[..., 0]
            out[..., 3] = 0
            return out
        r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]   # RGB565, little-endian
        v = ((r.astype(np.uint16) >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
        return v.view(np.uint8).reshape(h, w, 2) if v.dtype == np.uint16 \
            else np.stack([(v & 0xFF).astype(np.uint8), (v >> 8).astype(np.uint8)], -1)

    def blit(self, rgb):
        """Write a frame to the framebuffer's top-left. Clips to the fb if oversized.
        With vsync, blocks until the next vertical blank first (tear-free, ~60 Hz)."""
        if self._vbl is not None:
            self._vbl.wait()
        h = min(rgb.shape[0], self.fb_h)
        w = min(rgb.shape[1], self.fb_w)
        fb = self._to_fb(np.ascontiguousarray(rgb[:h, :w]))
        rowbytes = w * self.Bpp
        if rowbytes == self.stride:          # full-width fast path: one contiguous write
            self._mm[0:h * self.stride] = fb.tobytes()
        else:
            data = fb.tobytes()
            for y in range(h):
                off = y * self.stride
                self._mm[off:off + rowbytes] = data[y * rowbytes:(y + 1) * rowbytes]

    # Symmetry with the transport sinks (SpiOut/PioOut expose send/close); run_kms
    # calls blit() directly, but close() lets teardown be uniform.
    def close(self):
        try:
            if self._vbl is not None:
                self._vbl.close()
            self._mm.close()
        finally:
            os.close(self._fd)
