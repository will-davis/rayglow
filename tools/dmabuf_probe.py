"""DMA-BUF zero-copy readback feasibility probe (optimization-paths.md item 2).

Answers, empirically on the Pi, whether replacing `glReadPixels` with a
dma-heap render target + mmap is (a) correct and (b) faster:

  1. Allocate a buffer from /dev/dma_heap/system (cached CPU mmap, explicit
     cache maintenance via DMA_BUF_IOCTL_SYNC — the crucial difference from
     the PBO experiment, whose mapping was uncached and made reads SLOWER).
  2. Import it into EGL as a LINEAR ABGR8888 EGLImage
     (EGL_EXT_image_dma_buf_import; the extension probe confirmed V3D offers
     LINEAR with external_only=0, i.e. renderable — the TLB stores raster
     format natively).
  3. Bind as renderbuffer -> FBO, render the image pass into it directly.
  4. Fence (EGL_KHR_fence_sync), DMA_BUF_IOCTL_SYNC(START_READ) to
     invalidate CPU cache lines, then run the numpy postprocess straight out
     of the mapped buffer. No copy, no detile.

Benchmarks three modes against each other at the production geometry:
  glread       — the current Readback path (glReadPixels + postprocess)
  dmabuf       — render -> fence wait -> postprocess from mmap (zero latency)
  dmabuf-pipe  — ping-pong two dmabufs, postprocess frame N-1 while the GPU
                 renders frame N (PBO-style overlap, one frame of latency,
                 but through a CACHED mapping)

Also byte-compares dmabuf output against glReadPixels on a deterministic
frame — they must be identical.

Run ON THE PI (no root needed: dma_heap is video-group, DRI is render-group):
    ~/venv/bin/python ~/rayglow/tools/dmabuf_probe.py [--scale 2] [--frames 300]
"""
import argparse
import ctypes
import fcntl
import mmap
import os
import struct
import sys
import time
from ctypes import POINTER, byref, c_char_p, c_int, c_uint, c_uint64, c_void_p

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from rayglow.render import egl, passes  # noqa: E402
from rayglow.render.output import Readback  # noqa: E402
from rayglow.render.pipeline import _UniformState  # noqa: E402

# ---------------------------------------------------------------------------
# Constants not in egl.py (probe-local; promoted to egl.py if this ships)
# ---------------------------------------------------------------------------
EGL_LINUX_DMA_BUF_EXT = 0x3270
EGL_LINUX_DRM_FOURCC_EXT = 0x3271
EGL_DMA_BUF_PLANE0_FD_EXT = 0x3272
EGL_DMA_BUF_PLANE0_OFFSET_EXT = 0x3273
EGL_DMA_BUF_PLANE0_PITCH_EXT = 0x3274
EGL_DMA_BUF_PLANE0_MODIFIER_LO_EXT = 0x3443
EGL_DMA_BUF_PLANE0_MODIFIER_HI_EXT = 0x3444
EGL_WIDTH = 0x3057
EGL_HEIGHT = 0x3056
EGL_NONE = 0x3038
EGL_SYNC_FENCE_KHR = 0x30F9
EGL_SYNC_FLUSH_COMMANDS_BIT_KHR = 0x0001
EGL_FOREVER_KHR = 0xFFFFFFFFFFFFFFFF
EGL_CONDITION_SATISFIED_KHR = 0x30F6

GL_RENDERBUFFER = 0x8D41

FOURCC_ABGR8888 = 0x34324241        # 'AB24' — bytes R,G,B,A in memory
DRM_FORMAT_MOD_LINEAR = 0

# dma-heap / dma-buf ioctls (linux/dma-heap.h, linux/dma-buf.h)
DMA_HEAP_IOCTL_ALLOC = 0xC0184800   # _IOWR('H', 0, {u64 len; u32 fd; u32 fd_flags; u64 heap_flags})
DMA_BUF_IOCTL_SYNC = 0x40086200     # _IOW('b', 0, {u64 flags})
DMA_BUF_SYNC_READ = 1 << 0
DMA_BUF_SYNC_START = 0
DMA_BUF_SYNC_END = 1 << 2

# ---------------------------------------------------------------------------
# Extension function bindings
# ---------------------------------------------------------------------------
eglGetProcAddress = egl._bind(egl._egl, "eglGetProcAddress", c_void_p, [c_char_p])


def _ext(name, restype, argtypes):
    addr = eglGetProcAddress(name.encode())
    if not addr:
        raise egl.GLError(f"{name} not available")
    return ctypes.CFUNCTYPE(restype, *argtypes)(addr)


eglCreateImageKHR = _ext("eglCreateImageKHR", c_void_p,
                         [c_void_p, c_void_p, c_uint, c_void_p, POINTER(c_int)])
eglDestroyImageKHR = _ext("eglDestroyImageKHR", c_uint, [c_void_p, c_void_p])
glEGLImageTargetRenderbufferStorageOES = _ext(
    "glEGLImageTargetRenderbufferStorageOES", None, [c_uint, c_void_p])
eglCreateSyncKHR = _ext("eglCreateSyncKHR", c_void_p,
                        [c_void_p, c_uint, POINTER(c_int)])
eglClientWaitSyncKHR = _ext("eglClientWaitSyncKHR", c_int,
                            [c_void_p, c_void_p, c_int, c_uint64])
eglDestroySyncKHR = _ext("eglDestroySyncKHR", c_uint, [c_void_p, c_void_p])

glGenRenderbuffers = egl._bind(egl._gl, "glGenRenderbuffers", None,
                               [c_int, POINTER(c_uint)])
glBindRenderbuffer = egl._bind(egl._gl, "glBindRenderbuffer", None,
                               [c_uint, c_uint])
glDeleteRenderbuffers = egl._bind(egl._gl, "glDeleteRenderbuffers", None,
                                  [c_int, POINTER(c_uint)])
glFramebufferRenderbuffer = egl._bind(egl._gl, "glFramebufferRenderbuffer",
                                      None, [c_uint, c_uint, c_uint, c_uint])
glFlush = egl._bind(egl._gl, "glFlush", None, [])


# ---------------------------------------------------------------------------
# dma-heap allocation + EGL import
# ---------------------------------------------------------------------------
def heap_alloc(size, heap="system"):
    """Allocate `size` bytes from /dev/dma_heap/<heap>; returns the dmabuf fd."""
    heap_fd = os.open(f"/dev/dma_heap/{heap}", os.O_RDWR | os.O_CLOEXEC)
    try:
        # struct dma_heap_allocation_data { u64 len; u32 fd; u32 fd_flags; u64 heap_flags; }
        arg = bytearray(struct.pack("QIIQ", size, 0, os.O_RDWR | os.O_CLOEXEC, 0))
        fcntl.ioctl(heap_fd, DMA_HEAP_IOCTL_ALLOC, arg)
        _, fd, _, _ = struct.unpack("QIIQ", arg)
        return fd
    finally:
        os.close(heap_fd)


def dmabuf_sync(fd, flags):
    fcntl.ioctl(fd, DMA_BUF_IOCTL_SYNC, struct.pack("Q", flags))


class DmaBufTarget:
    """One dma-heap buffer imported as a LINEAR renderable EGLImage + FBO.

    `view` is a (h, w, 4) uint8 numpy array over the CACHED mmap of the
    buffer. Bracket CPU reads with begin_read()/end_read() — START_READ
    invalidates the CPU cache for the range so we see what V3D wrote.
    """

    def __init__(self, display, width, height, heap="system"):
        self.display = display
        self.w, self.h = width, height
        self.pitch = width * 4
        size = (self.pitch * height + 4095) & ~4095
        self.fd = heap_alloc(size, heap)
        self.mm = mmap.mmap(self.fd, size, mmap.MAP_SHARED, mmap.PROT_READ)
        self.view = np.frombuffer(self.mm, np.uint8,
                                  self.pitch * height).reshape(height, width, 4)

        attribs = (c_int * 17)(
            EGL_WIDTH, width,
            EGL_HEIGHT, height,
            EGL_LINUX_DRM_FOURCC_EXT, FOURCC_ABGR8888,
            EGL_DMA_BUF_PLANE0_FD_EXT, self.fd,
            EGL_DMA_BUF_PLANE0_OFFSET_EXT, 0,
            EGL_DMA_BUF_PLANE0_PITCH_EXT, self.pitch,
            EGL_DMA_BUF_PLANE0_MODIFIER_LO_EXT, DRM_FORMAT_MOD_LINEAR,
            EGL_DMA_BUF_PLANE0_MODIFIER_HI_EXT, 0,
            EGL_NONE)
        self.image = eglCreateImageKHR(display, None, EGL_LINUX_DMA_BUF_EXT,
                                       None, attribs)
        if not self.image:
            raise egl.GLError(
                f"eglCreateImageKHR failed (0x{egl.eglGetError():04X}) "
                f"for {width}x{height} LINEAR ABGR8888 from heap '{heap}'")

        rbo = c_uint(0)
        glGenRenderbuffers(1, byref(rbo))
        self.rbo = rbo.value
        glBindRenderbuffer(GL_RENDERBUFFER, self.rbo)
        glEGLImageTargetRenderbufferStorageOES(GL_RENDERBUFFER, self.image)
        egl.check_gl("EGLImage -> renderbuffer")

        fbo = c_uint(0)
        egl.glGenFramebuffers(1, byref(fbo))
        self.fbo = fbo.value
        egl.glBindFramebuffer(egl.GL_FRAMEBUFFER, self.fbo)
        glFramebufferRenderbuffer(egl.GL_FRAMEBUFFER, egl.GL_COLOR_ATTACHMENT0,
                                  GL_RENDERBUFFER, self.rbo)
        status = egl.glCheckFramebufferStatus(egl.GL_FRAMEBUFFER)
        if status != egl.GL_FRAMEBUFFER_COMPLETE:
            raise egl.GLError(f"dmabuf FBO incomplete: 0x{status:04X}")

    def begin_read(self):
        dmabuf_sync(self.fd, DMA_BUF_SYNC_START | DMA_BUF_SYNC_READ)

    def end_read(self):
        dmabuf_sync(self.fd, DMA_BUF_SYNC_END | DMA_BUF_SYNC_READ)

    def destroy(self):
        egl.delete_framebuffer(self.fbo)
        glDeleteRenderbuffers(1, byref(c_uint(self.rbo)))
        eglDestroyImageKHR(self.display, self.image)
        del self.view
        self.mm.close()
        os.close(self.fd)


class Fence:
    """EGL fence sync: created after queueing GPU work, wait() blocks until
    that work completes (FLUSH_COMMANDS submits it on create)."""

    def __init__(self, display):
        self.display = display
        self.sync = eglCreateSyncKHR(display, EGL_SYNC_FENCE_KHR, None)
        if not self.sync:
            raise egl.GLError(f"eglCreateSyncKHR failed (0x{egl.eglGetError():04X})")

    def wait(self):
        r = eglClientWaitSyncKHR(self.display, self.sync,
                                 EGL_SYNC_FLUSH_COMMANDS_BIT_KHR,
                                 EGL_FOREVER_KHR)
        if r != EGL_CONDITION_SATISFIED_KHR:
            raise egl.GLError(f"eglClientWaitSyncKHR returned 0x{r:04X}")
        eglDestroySyncKHR(self.display, self.sync)
        self.sync = None


# ---------------------------------------------------------------------------
# Benchmark
# ---------------------------------------------------------------------------
# Moderately heavy plasma — representative of the preset library's ALU load.
SHADER = """
void mainImage(out vec4 fragColor, in vec2 fragCoord) {
    vec2 uv = fragCoord / iResolution.xy;
    vec2 p = uv * 8.0 - 4.0;
    float v = 0.0;
    for (int i = 0; i < 48; i++) {
        float fi = float(i);
        p = vec2(p.y + sin(p.x * 1.3 + iTime + fi * 0.13),
                 p.x - cos(p.y * 1.7 - iTime * 0.7 + fi * 0.11));
        v += sin(length(p) * 0.8 + fi * 0.2) / 48.0;
    }
    fragColor = vec4(0.5 + 0.5 * sin(v * 6.28318 + vec3(0.0, 2.09, 4.18)), 1.0);
}
"""

# GPU resolve pass (optimization-paths.md item 3, prototyped here): box-average
# the supersampled image texture down to panel resolution, apply gamma, and
# flip so memory row 0 = wall top row — the readback then needs NO CPU
# postprocess at all. Written as a mainImage shader so the probe (and the
# production renderer) reuse the existing Pass machinery; the constants are
# baked in by str.format because they're fixed for the life of the process.
RESOLVE_SHADER = """
void mainImage(out vec4 o, in vec2 fc) {{
    ivec2 d = ivec2(fc);
    int y0 = ({h} - 1 - d.y) * {s};
    int x0 = d.x * {s};
    vec3 a = vec3(0.0);
    for (int j = 0; j < {s}; j++)
        for (int i = 0; i < {s}; i++)
            a += texelFetch(iChannel0, ivec2(x0 + i, y0 + j), 0).rgb;
    o = vec4({gamma_expr}, 1.0);
}}
"""


def make_resolve_pass(width, height, scale, src_tex, dummy, gamma=1.0):
    """Panel-resolution resolve Pass sampling the image pass's texture."""
    a = f"a * {1.0 / (scale * scale):.10f}"
    expr = a if gamma == 1.0 else f"pow({a}, vec3({gamma}))"
    rp = passes.Pass("resolve", width, height, dummy)
    ok, msg = rp.compile(RESOLVE_SHADER.format(h=height, s=scale,
                                               gamma_expr=expr))
    if not ok:
        raise egl.GLError(f"resolve shader: {msg}")
    rp.channels[0] = passes.Channel("texture", src_tex)
    return rp


def make_state(i, fps=120.0):
    st = _UniformState()
    st.time, st.dt, st.frame = i / fps, 1.0 / fps, i
    st.frame_rate = fps
    st.mouse = (0.0, 0.0, 0.0, 0.0)
    st.date = (2026.0, 6.0, 13.0, 43200.0)
    return st


def stats(name, samples):
    a = np.asarray(samples) * 1e3
    print(f"  {name:<28s} mean {a.mean():6.2f}ms  p50 {np.percentile(a, 50):6.2f}"
          f"  p99 {np.percentile(a, 99):6.2f}")


def bench(label, frames, fn):
    fn(make_state(0))                          # warm
    for i in range(1, 30):
        fn(make_state(i))
    t = []
    for i in range(30, 30 + frames):
        t0 = time.perf_counter()
        fn(make_state(i))
        t.append(time.perf_counter() - t0)
    print(f"{label}:")
    stats("frame total", t)
    return np.asarray(t)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--width", type=int, default=256)
    ap.add_argument("--height", type=int, default=64)
    ap.add_argument("--scale", type=int, default=2)
    ap.add_argument("--frames", type=int, default=300)
    ap.add_argument("--heap", default="system",
                    help="dma-heap name (default 'system'; try 'linux,cma')")
    args = ap.parse_args()
    w, h, s = args.width, args.height, args.scale
    rw, rh = w * s, h * s

    ctx = egl.GLContext()
    print(f"GPU: {ctx.info()}")
    print(f"render {rw}x{rh} (scale {s}) -> wall {w}x{h}, "
          f"heap '{args.heap}', {args.frames} frames/mode\n")

    dummy = passes.make_texture(1, 1, bytes(4))
    p = passes.Pass("image", rw, rh, dummy)
    ok, msg = p.compile(SHADER)
    if not ok:
        print(msg, file=sys.stderr)
        sys.exit(1)
    rb = Readback(w, h, s, gamma=1.0)
    tex_fbo = p.fbo                            # the normal texture-backed FBO

    targets = [DmaBufTarget(ctx.display, rw, rh, args.heap) for _ in range(2)]
    print("dmabuf import + renderbuffer FBO: OK\n")

    # -- correctness: same deterministic frame through both paths ----------
    st = make_state(7)
    p.fbo = tex_fbo
    p.render(st)
    ref = rb.read(tex_fbo)

    p.fbo = targets[0].fbo
    p.render(st)
    Fence(ctx.display).wait()
    targets[0].begin_read()
    got = rb._postprocess(targets[0].view)
    targets[0].end_read()
    if np.array_equal(ref, got):
        print(f"correctness: dmabuf output IDENTICAL to glReadPixels "
              f"({ref.shape}, {ref.nbytes} bytes)\n")
    else:
        diff = np.abs(ref.astype(int) - got.astype(int))
        print(f"correctness: MISMATCH — max delta {diff.max()}, "
              f"{np.count_nonzero(diff.any(axis=2))} px differ\n")

    # -- mode 1: production glReadPixels path -------------------------------
    def run_glread(st):
        p.fbo = tex_fbo
        p.render(st)
        return rb.read(tex_fbo)

    t_glread = bench("glread (production)", args.frames, run_glread)

    # -- mode 2: dmabuf, synchronous (zero added latency) --------------------
    sub_render, sub_wait, sub_post = [], [], []

    def run_dmabuf(st):
        tgt = targets[0]
        t0 = time.perf_counter()
        p.fbo = tgt.fbo
        p.render(st)
        f = Fence(ctx.display)
        t1 = time.perf_counter()
        f.wait()
        t2 = time.perf_counter()
        tgt.begin_read()
        out = rb._postprocess(tgt.view)
        tgt.end_read()
        t3 = time.perf_counter()
        sub_render.append(t1 - t0)
        sub_wait.append(t2 - t1)
        sub_post.append(t3 - t2)
        return out

    t_dmabuf = bench("dmabuf (sync)", args.frames, run_dmabuf)
    n = args.frames
    stats("submit", sub_render[-n:])
    stats("fence wait", sub_wait[-n:])
    stats("sync-ioctl + postprocess", sub_post[-n:])

    # -- mode 3: dmabuf ping-pong (one frame latency, overlap) ---------------
    pending = {}                                # slot -> Fence

    def run_pipe(st):
        i = st.frame & 1
        cur, prev = targets[i], targets[i ^ 1]
        p.fbo = cur.fbo
        p.render(st)
        pending[i] = Fence(ctx.display)
        glFlush()
        f = pending.pop(i ^ 1, None)
        if f is None:
            return None                        # prime frame
        f.wait()                               # usually already signaled
        prev.begin_read()
        out = rb._postprocess(prev.view)
        prev.end_read()
        return out

    t_pipe = bench("dmabuf (ping-pong, 1 frame latency)", args.frames, run_pipe)

    # -- mode 4/5: GPU resolve pass into a panel-resolution dmabuf ------------
    # image pass -> texture FBO (full render size), resolve pass -> W x H
    # dmabuf. CPU postprocess collapses to a 48 KB slice-copy.
    resolve = make_resolve_pass(w, h, s, p.out_tex, dummy)
    rtargets = [DmaBufTarget(ctx.display, w, h, args.heap) for _ in range(2)]

    # numeric equivalence vs the CPU postprocess (float avg vs uint16 box-sum
    # LUT: expect <= 1 LSB)
    st = make_state(7)
    p.fbo = tex_fbo
    p.render(st)
    ref = rb.read(tex_fbo)
    resolve.fbo = rtargets[0].fbo
    resolve.render(st)
    Fence(ctx.display).wait()
    rtargets[0].begin_read()
    got = np.ascontiguousarray(rtargets[0].view[:, :, :3])
    rtargets[0].end_read()
    diff = np.abs(ref.astype(int) - got.astype(int))
    print(f"\nresolve-pass equivalence vs CPU postprocess: max delta "
          f"{diff.max()} LSB ({np.count_nonzero(diff)} of {diff.size} "
          f"subpixels differ)\n")

    def run_resolve(st):
        tgt = rtargets[0]
        p.fbo = tex_fbo
        p.render(st)
        resolve.fbo = tgt.fbo
        resolve.render(st)
        Fence(ctx.display).wait()
        tgt.begin_read()
        out = np.ascontiguousarray(tgt.view[:, :, :3])
        tgt.end_read()
        return out

    t_res = bench("dmabuf + GPU resolve (sync)", args.frames, run_resolve)

    rpending = {}

    def run_resolve_pipe(st):
        i = st.frame & 1
        cur, prev = rtargets[i], rtargets[i ^ 1]
        p.fbo = tex_fbo
        p.render(st)
        resolve.fbo = cur.fbo
        resolve.render(st)
        rpending[i] = Fence(ctx.display)
        glFlush()
        f = rpending.pop(i ^ 1, None)
        if f is None:
            return None
        f.wait()
        prev.begin_read()
        out = np.ascontiguousarray(prev.view[:, :, :3])
        prev.end_read()
        return out

    t_res_pipe = bench("dmabuf + GPU resolve (ping-pong)", args.frames,
                       run_resolve_pipe)

    base = t_glread.mean()
    print(f"\nvs glread: dmabuf-sync {base / t_dmabuf.mean():.2f}x, "
          f"pipelined {base / t_pipe.mean():.2f}x, "
          f"resolve-sync {base / t_res.mean():.2f}x, "
          f"resolve-pipe {base / t_res_pipe.mean():.2f}x")

    resolve.destroy()
    for tgt in rtargets:
        tgt.destroy()
    for tgt in targets:
        tgt.destroy()
    rb.destroy()
    p.destroy()
    egl.delete_texture(dummy)
    ctx.destroy()
    print("probe done")


if __name__ == "__main__":
    main()
