"""Frame output: GPU resolve pass + readback strategies, and dry-run sinks.

Two generations coexist here:

- The CURRENT path: a GPU "resolve" pass (make_resolve) box-averages the
  supersampled image texture down to panel resolution, applies gamma, and
  orients the frame so memory row 0 is the wall's top row — the CPU
  postprocess stage disappears entirely.  A reader (make_reader) then hands
  the 64 KB frame to Python: DmabufReader renders straight into a dma-heap
  buffer and reads it through a cached mmap (zero-copy, see dmabuf.py);
  GlReadResolveReader is the portable glReadPixels fallback (desktop
  dry-runs, non-Mesa stacks).  Measured on the Pi 5 (tools/dmabuf_probe.py):
  2.4x faster than the legacy path at scale 2, with no added latency.

- The LEGACY path (--readback legacy): full-size glReadPixels + numpy
  box-sum/LUT/flip postprocess (class Readback below), kept as a regression
  escape hatch and for the PBO provenance notes.
"""
import ctypes

import numpy as np

from . import egl
from . import passes
from .egl import (GL_FRAMEBUFFER, GL_MAP_READ_BIT, GL_PIXEL_PACK_BUFFER,
                  GL_RGBA, GL_STREAM_READ, GL_UNSIGNED_BYTE)


# ---------------------------------------------------------------------------
# GPU resolve pass
# ---------------------------------------------------------------------------
# Downsample + gamma + orientation in one fragment shader (items 3+4+8 of
# docs/design-history/2026-07-13-optimization-paths.md).  Written as a
# Shadertoy mainImage so it reuses the Pass
# machinery; the constants are baked in with str.format because they are
# fixed for the life of the process (hot reload never touches this pass).
#
# Row mapping: glReadPixels and a linear dmabuf both lay out GL row 0 (bottom)
# first in memory, and the wall wants row 0 = top — so output row y samples
# source rows [(H-1-y)*s, (H-y)*s), i.e. the vertical flip that the legacy
# CPU postprocess did with [::-1] is folded into the sampling coordinates.
# config.FLIP_V/FLIP_H (physical mount compensation) fold in the same way.
RESOLVE_SRC = """
void mainImage(out vec4 o, in vec2 fc) {{
    ivec2 d = ivec2(fc);
    int y0 = ({y_expr}) * {s};
    int x0 = ({x_expr}) * {s};
    vec3 a = vec3(0.0);
    for (int j = 0; j < {s}; j++)
        for (int i = 0; i < {s}; i++)
            a += texelFetch(iChannel0, ivec2(x0 + i, y0 + j), 0).rgb;
    o = vec4({gamma_expr}, 1.0);
}}
"""


def make_resolve(width, height, scale, gamma, src_tex, dummy_tex,
                 flip=(False, False)):
    """Build the panel-resolution resolve Pass sampling `src_tex` (the image
    pass's supersampled output texture).

    gamma != 1.0 bakes `pow(avg, gamma)` into the shader — quantization to
    8 bits then happens ONCE, from float, instead of the legacy path's
    8-bit-readback-then-8-bit-LUT double quantization (better dark-end
    gradients).  The packer must then be fed an identity LUT
    (hub75.LUT_IDENTITY) or gamma double-corrects.

    flip = (flip_v, flip_h): config.FLIP_V/FLIP_H for the wall path; keep
    (False, False) for dry-runs, which historically never applied mount flips.
    """
    flip_v, flip_h = flip
    a = f"a * {1.0 / (scale * scale):.10f}"
    src = RESOLVE_SRC.format(
        s=scale,
        y_expr=("d.y" if flip_v else f"{height} - 1 - d.y"),
        x_expr=(f"{width} - 1 - d.x" if flip_h else "d.x"),
        gamma_expr=(a if gamma == 1.0 else f"pow({a}, vec3({gamma!r}))"))
    rp = passes.Pass("resolve", width, height, dummy_tex)
    ok, msg = rp.compile(src)
    if not ok:
        raise egl.GLError(f"resolve shader failed to compile (bug): {msg}")
    rp.channels[0] = passes.Channel("texture", src_tex,
                                    width * scale, height * scale)
    return rp


# ---------------------------------------------------------------------------
# Readers: resolve-pass FBO -> (H, W, 3) uint8
# ---------------------------------------------------------------------------
class GlReadResolveReader:
    """Portable reader: glReadPixels the resolve pass's own texture FBO.

    Still a big win over legacy — the read is 64 KB of final pixels instead
    of the supersampled frame, and there is no CPU postprocess.
    """

    def __init__(self, resolve_fbo, width, height):
        self._fbo = resolve_fbo
        self.w, self.h = width, height
        self._buf = np.empty((height, width, 4), np.uint8)

    def target_fbo(self, frame):
        return self._fbo

    def read(self, frame):
        egl.glBindFramebuffer(GL_FRAMEBUFFER, self._fbo)
        egl.glReadPixels(0, 0, self.w, self.h, GL_RGBA, GL_UNSIGNED_BYTE,
                         self._buf.ctypes.data_as(ctypes.c_void_p))
        return np.ascontiguousarray(self._buf[:, :, :3])

    def destroy(self):
        pass                        # the FBO belongs to the resolve pass


class DmabufReader:
    """Zero-copy reader: the resolve pass renders into a dma-heap buffer and
    the CPU reads it through a cached mmap (see dmabuf.py for why this beats
    both glReadPixels and PBOs on V3D).

    pipelined=False (default): fence-wait this frame, read it.  No added
    latency; the fence covers GPU work that glReadPixels would also have
    waited for.

    pipelined=True: ping-pong two buffers and read frame N-1 while the GPU
    renders frame N — hides the whole GPU render behind the CPU pack stage
    at the cost of ONE frame of visual latency.  The first frame (and the
    first after a --loop switch) is read synchronously so callers never see
    a missing frame.
    """

    def __init__(self, width, height, pipelined=False, heap="system"):
        from .dmabuf import DmaBufTarget
        self.pipelined = pipelined
        n = 2 if pipelined else 1
        self.targets = [DmaBufTarget(width, height, heap) for _ in range(n)]
        self._fences = {}

    def target_fbo(self, frame):
        return self.targets[frame % len(self.targets)].fbo

    def _read_target(self, tgt):
        tgt.begin_read()
        out = np.ascontiguousarray(tgt.view[:, :, :3])
        tgt.end_read()
        return out

    def read(self, frame):
        from .dmabuf import Fence
        if not self.pipelined:
            Fence().wait()
            return self._read_target(self.targets[0])
        i = frame % 2
        self._fences[i] = Fence()
        egl.glFlush()
        prev = self._fences.pop(i ^ 1, None)
        if prev is None:            # prime frame: read this one synchronously
            self._fences.pop(i).wait()
            return self._read_target(self.targets[i])
        prev.wait()                 # normally already signaled
        return self._read_target(self.targets[i ^ 1])

    def destroy(self):
        for f in self._fences.values():
            f.wait()
        self._fences = {}
        for t in self.targets:
            t.destroy()


def make_reader(mode, resolve_fbo, width, height):
    """Build the reader for --readback `mode` ('auto', 'dmabuf',
    'dmabuf-pipe', 'glread').  Returns (reader, description).  'auto' tries
    the zero-copy dmabuf path and falls back to glReadPixels — expected on
    non-Mesa/non-dma-heap stacks like the desktop's EGL."""
    if mode in ("dmabuf", "dmabuf-pipe", "auto"):
        try:
            r = DmabufReader(width, height, pipelined=(mode == "dmabuf-pipe"))
            return r, ("dmabuf zero-copy"
                       + (" (pipelined, +1 frame latency)"
                          if mode == "dmabuf-pipe" else ""))
        except (OSError, egl.GLError) as e:
            if mode != "auto":
                raise
            fallback_note = f" (dmabuf unavailable: {e})"
    else:
        fallback_note = ""
    return (GlReadResolveReader(resolve_fbo, width, height),
            "glReadPixels" + fallback_note)


class Readback:
    """Preallocated glReadPixels target + flip/downsample/gamma postprocess.

    Pipeline: read scale*(W,H) RGBA -> box-filter downsample -> gamma (repo
    convention: out = clip(x)**gamma * 255) -> vertical flip (GL origin is
    bottom-left) -> drop alpha -> contiguous (H,W,3) uint8 for SetImage.

    Perf: the naive float .mean() over the supersampled buffer cost ~13ms per
    frame at scale 4.  Instead we integer-sum the s*s box (fits uint16: 64
    samples * 255 max) on the *contiguous* RGBA buffer and apply gamma via a
    precomputed LUT indexed by that sum — exact and ~10x faster.

    `use_pbo` double-buffers the readback through two pixel-pack buffer objects:
    glReadPixels into PBO[cur] returns immediately, then we map PBO[other]
    holding *last* frame's pixels (one frame of latency; first call returns a
    black prime frame). This is a DISCRETE-GPU optimization and is **off by
    default** because it measured ~2.5-5ms SLOWER on the Pi's V3D: unified memory
    means glReadPixels isn't a bus stall to hide, and the box-sum then streams
    the supersampled frame out of the *uncached* mapped buffer (vs the cached
    numpy buffer the sync path fills). Kept behind `--pbo` for provenance / other
    GPUs. The synchronous path is the default and the faster one here.
    """

    def __init__(self, width, height, scale, gamma, use_pbo=False):
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

    def destroy(self):
        """Free the PBO pair (sync path owns no GL objects)."""
        if self.use_pbo:
            ids = (ctypes.c_uint * 2)(self._pbo[0], self._pbo[1])
            egl.glDeleteBuffers(2, ids)

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
