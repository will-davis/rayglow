"""Frame postprocessing (GPU readback -> panel-ready uint8) and dry-run sinks."""
import ctypes

import numpy as np

from . import egl
from .egl import (GL_FRAMEBUFFER, GL_MAP_READ_BIT, GL_PIXEL_PACK_BUFFER,
                  GL_RGBA, GL_STREAM_READ, GL_UNSIGNED_BYTE)


class Readback:
    """Preallocated glReadPixels target + flip/downsample/gamma postprocess.

    Pipeline: read scale*(W,H) RGBA -> box-filter downsample -> gamma (repo
    convention: out = clip(x)**gamma * 255) -> vertical flip (GL origin is
    bottom-left) -> drop alpha -> contiguous (H,W,3) uint8 for SetImage.

    Perf: the naive float .mean() over the supersampled buffer cost ~13ms per
    frame at scale 4.  Instead we integer-sum the s*s box (fits uint16: 64
    samples * 255 max) on the *contiguous* RGBA buffer and apply gamma via a
    precomputed LUT indexed by that sum — exact and ~10x faster.

    `use_pbo` (default on) double-buffers the readback through two pixel-pack
    buffer objects: glReadPixels into PBO[cur] returns immediately (async GPU
    DMA, no CPU stall), and we map PBO[other] holding *last* frame's pixels.
    Costs one frame of latency (fine for a visualizer); the first call returns
    a black frame to prime the pipeline. Set use_pbo=False for the exact,
    zero-latency synchronous path (used by dry-run so the GIF stays frame-exact).
    """

    def __init__(self, width, height, scale, gamma, use_pbo=True):
        if not 1 <= scale <= 16:
            raise ValueError("scale must be in 1..16")
        self.w, self.h, self.scale = width, height, scale
        self.use_pbo = use_pbo
        self._buf = np.empty((height * scale, width * scale, 4), np.uint8)
        self._nbytes = self._buf.nbytes
        # LUT over all possible box sums: sum in [0, s*s*255].
        sums = np.arange(scale * scale * 255 + 1, dtype=np.float32)
        x = sums / (scale * scale * 255.0)
        self._lut = (x ** gamma * 255.0 + 0.5).astype(np.uint8)
        if use_pbo:
            ids = (ctypes.c_uint * 2)()
            egl.glGenBuffers(2, ids)
            self._pbo = [ids[0], ids[1]]
            for b in self._pbo:
                egl.glBindBuffer(GL_PIXEL_PACK_BUFFER, b)
                egl.glBufferData(GL_PIXEL_PACK_BUFFER, self._nbytes, None,
                                 GL_STREAM_READ)
            egl.glBindBuffer(GL_PIXEL_PACK_BUFFER, 0)
            self._cur = 0        # PBO that THIS frame's readback writes into
            self._primed = False
            self._blank = np.zeros((height, width, 3), np.uint8)
            egl.check_gl("PBO readback init")

    def _postprocess(self, rgba):
        """box-sum downsample -> gamma LUT -> v-flip -> drop alpha. Returns a
        fresh contiguous (H,W,3) uint8 (copies out of any mapped buffer)."""
        s = self.scale
        if s > 1:
            boxed = rgba.reshape(self.h, s, self.w, s, 4).sum(
                axis=(1, 3), dtype=np.uint16)
        else:
            boxed = rgba
        frame = self._lut[boxed[::-1, :, :3]]      # fancy-index -> fresh array
        return np.ascontiguousarray(frame)

    def read(self, fbo):
        s = self.scale
        egl.glBindFramebuffer(GL_FRAMEBUFFER, fbo)
        if not self.use_pbo:
            egl.glReadPixels(0, 0, self.w * s, self.h * s,
                             GL_RGBA, GL_UNSIGNED_BYTE,
                             self._buf.ctypes.data_as(ctypes.c_void_p))
            return self._postprocess(self._buf)

        # Async path: kick this frame's readback into the current PBO (returns
        # immediately — offset 0 because a PBO is bound), then read the OTHER
        # PBO, which has held last frame's pixels for a full frame (DMA done, so
        # the map doesn't stall).
        egl.glBindBuffer(GL_PIXEL_PACK_BUFFER, self._pbo[self._cur])
        egl.glReadPixels(0, 0, self.w * s, self.h * s,
                         GL_RGBA, GL_UNSIGNED_BYTE, 0)
        other = self._cur ^ 1
        self._cur = other        # flip for next frame
        if not self._primed:
            self._primed = True
            egl.glBindBuffer(GL_PIXEL_PACK_BUFFER, 0)
            return self._blank
        egl.glBindBuffer(GL_PIXEL_PACK_BUFFER, self._pbo[other])
        ptr = egl.glMapBufferRange(GL_PIXEL_PACK_BUFFER, 0, self._nbytes,
                                   GL_MAP_READ_BIT)
        if not ptr:
            raise egl.GLError("glMapBufferRange returned NULL")
        mapped = np.frombuffer(
            (ctypes.c_ubyte * self._nbytes).from_address(ptr), dtype=np.uint8
        ).reshape(self.h * s, self.w * s, 4)
        frame = self._postprocess(mapped)          # copies before unmap
        egl.glUnmapBuffer(GL_PIXEL_PACK_BUFFER)
        egl.glBindBuffer(GL_PIXEL_PACK_BUFFER, 0)
        return frame


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
