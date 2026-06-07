"""Frame postprocessing (GPU readback -> panel-ready uint8) and dry-run sinks."""
import ctypes

import numpy as np

from . import egl
from .egl import GL_FRAMEBUFFER, GL_RGBA, GL_UNSIGNED_BYTE


class Readback:
    """Preallocated glReadPixels target + flip/downsample/gamma postprocess.

    Pipeline: read scale*(W,H) RGBA -> box-filter downsample -> gamma (repo
    convention: out = clip(x)**gamma * 255) -> vertical flip (GL origin is
    bottom-left) -> drop alpha -> contiguous (H,W,3) uint8 for SetImage.

    Perf: the naive float .mean() over the supersampled buffer cost ~13ms per
    frame at scale 4.  Instead we integer-sum the s*s box (fits uint16: 64
    samples * 255 max) on the *contiguous* RGBA buffer and apply gamma via a
    precomputed LUT indexed by that sum — exact and ~10x faster.
    """

    def __init__(self, width, height, scale, gamma):
        if not 1 <= scale <= 16:
            raise ValueError("scale must be in 1..16")
        self.w, self.h, self.scale = width, height, scale
        self._buf = np.empty((height * scale, width * scale, 4), np.uint8)
        # LUT over all possible box sums: sum in [0, s*s*255].
        sums = np.arange(scale * scale * 255 + 1, dtype=np.float32)
        x = sums / (scale * scale * 255.0)
        self._lut = (x ** gamma * 255.0 + 0.5).astype(np.uint8)

    def read(self, fbo):
        s = self.scale
        egl.glBindFramebuffer(GL_FRAMEBUFFER, fbo)
        egl.glReadPixels(0, 0, self.w * s, self.h * s,
                         GL_RGBA, GL_UNSIGNED_BYTE,
                         self._buf.ctypes.data_as(ctypes.c_void_p))
        if s > 1:
            boxed = self._buf.reshape(self.h, s, self.w, s, 4).sum(
                axis=(1, 3), dtype=np.uint16)
        else:
            boxed = self._buf
        frame = self._lut[boxed[::-1, :, :3]]
        return np.ascontiguousarray(frame)


def save_gif(frames, path, fps, upscale=3):
    """Write dry-run frames as an animated GIF, nearest-upscaled so a 256x32
    strip is actually eyeballable."""
    from PIL import Image
    if upscale > 1:
        frames = [np.repeat(np.repeat(f, upscale, 0), upscale, 1)
                  for f in frames]
    imgs = [Image.fromarray(f, "RGB") for f in frames]
    imgs[0].save(path, save_all=True, append_images=imgs[1:],
                 duration=max(1, int(1000 / fps)), loop=0)
