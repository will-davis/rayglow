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
import mmap
import os

import numpy as np


def _sysfs_int(path, default):
    try:
        return int(open(path).read().strip())
    except OSError:
        return default


class KmsOut:
    """Open /dev/fb0 and blit (H, W, 3) uint8 RGB frames to its top-left corner."""

    def __init__(self, fbdev="/dev/fb0"):
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
        self.desc = (f"KMS /dev/fb0 {self.fb_w}x{self.fb_h} {self.bpp}bpp "
                     f"stride={self.stride}")

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
        """Write a frame to the framebuffer's top-left. Clips to the fb if oversized."""
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
            self._mm.close()
        finally:
            os.close(self._fd)
