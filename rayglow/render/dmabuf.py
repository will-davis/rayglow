"""dma-heap render targets: zero-copy GPU->CPU readback without glReadPixels.

The idea (validated end-to-end by tools/dmabuf_probe.py on the Pi 5): allocate
a plain buffer from /dev/dma_heap/system, import it into EGL as a LINEAR
ABGR8888 EGLImage (V3D's tile buffer stores raster format natively, so LINEAR
is renderable — the extension probe shows external_only=0), attach it to an
FBO via a renderbuffer, and render straight into it.  The CPU then reads the
pixels through a *cached* mmap of the same buffer — no glReadPixels detile,
no copy.

Why this wins where PBOs lost: Mesa maps PBOs uncached on V3D, so streaming
the frame out of a mapped PBO was measured SLOWER than the synchronous
glReadPixels copy (see output.Readback).  A dma-heap buffer gives a cached
CPU mapping with *explicit* cache maintenance instead: bracket reads with
DMA_BUF_IOCTL_SYNC(START/END, READ), which invalidates the CPU cache lines
for the range (V3D is not IO-coherent), then read at full cached-DRAM speed.

Synchronization: rendering must still finish before the CPU may look at the
buffer — that is inherent, not a glReadPixels artifact.  Fence (an
EGL_KHR_fence_sync wrapper) waits for exactly the work queued so far.

Requires (all present on Pi OS trixie / Mesa 25, checked by available()):
  - /dev/dma_heap/system readable (video group — no root needed)
  - EGL_EXT_image_dma_buf_import (+ _modifiers) with LINEAR ABGR8888
  - GL_OES_EGL_image, EGL_KHR_fence_sync
On other stacks (e.g. the desktop's NVIDIA EGL) construction fails cleanly
and output.make_reader falls back to glReadPixels.
"""
import fcntl
import mmap
import os
import struct
from ctypes import POINTER, byref, c_int, c_uint, c_uint64, c_void_p

import numpy as np

from . import egl

FOURCC_ABGR8888 = 0x34324241        # 'AB24' — bytes R,G,B,A in memory
DRM_FORMAT_MOD_LINEAR = 0

# linux/dma-heap.h: _IOWR('H', 0, struct dma_heap_allocation_data
#                         { u64 len; u32 fd; u32 fd_flags; u64 heap_flags; })
DMA_HEAP_IOCTL_ALLOC = 0xC0184800
# linux/dma-buf.h: _IOW('b', 0, struct dma_buf_sync { u64 flags })
DMA_BUF_IOCTL_SYNC = 0x40086200
DMA_BUF_SYNC_READ = 1 << 0
DMA_BUF_SYNC_START = 0
DMA_BUF_SYNC_END = 1 << 2

HEAP_DIR = "/dev/dma_heap"

# Extension entry points, bound lazily so importing this module never fails —
# a desktop dry-run must be able to *try* dmabuf and fall back.
_fns = None


def _ext_fns():
    global _fns
    if _fns is None:
        _fns = {
            "create_image": egl.load_ext(
                "eglCreateImageKHR", c_void_p,
                [c_void_p, c_void_p, c_uint, c_void_p, POINTER(c_int)]),
            "destroy_image": egl.load_ext(
                "eglDestroyImageKHR", c_uint, [c_void_p, c_void_p]),
            "rbo_storage": egl.load_ext(
                "glEGLImageTargetRenderbufferStorageOES", None,
                [c_uint, c_void_p]),
            "create_sync": egl.load_ext(
                "eglCreateSyncKHR", c_void_p,
                [c_void_p, c_uint, POINTER(c_int)]),
            "wait_sync": egl.load_ext(
                "eglClientWaitSyncKHR", c_int,
                [c_void_p, c_void_p, c_int, c_uint64]),
            "destroy_sync": egl.load_ext(
                "eglDestroySyncKHR", c_uint, [c_void_p, c_void_p]),
        }
    return _fns


def heap_alloc(size, heap="system"):
    """Allocate `size` bytes from /dev/dma_heap/<heap>; returns the dmabuf fd."""
    heap_fd = os.open(os.path.join(HEAP_DIR, heap), os.O_RDWR | os.O_CLOEXEC)
    try:
        arg = bytearray(struct.pack("QIIQ", size, 0,
                                    os.O_RDWR | os.O_CLOEXEC, 0))
        fcntl.ioctl(heap_fd, DMA_HEAP_IOCTL_ALLOC, arg)
        _, fd, _, _ = struct.unpack("QIIQ", arg)
        return fd
    finally:
        os.close(heap_fd)


def _dmabuf_sync(fd, flags):
    fcntl.ioctl(fd, DMA_BUF_IOCTL_SYNC, struct.pack("Q", flags))


class Fence:
    """One EGL fence sync.  Create AFTER queueing the GPU work you care
    about; wait() flushes the queue and blocks until that work completes."""

    def __init__(self):
        fns = _ext_fns()
        self._display = egl.eglGetCurrentDisplay()
        self.sync = fns["create_sync"](self._display, egl.EGL_SYNC_FENCE_KHR,
                                       None)
        if not self.sync:
            raise egl.GLError(
                f"eglCreateSyncKHR failed (0x{egl.eglGetError():04X})")

    def wait(self):
        fns = _ext_fns()
        r = fns["wait_sync"](self._display, self.sync,
                             egl.EGL_SYNC_FLUSH_COMMANDS_BIT_KHR,
                             egl.EGL_FOREVER_KHR)
        fns["destroy_sync"](self._display, self.sync)
        self.sync = None
        if r != egl.EGL_CONDITION_SATISFIED_KHR:
            raise egl.GLError(f"eglClientWaitSyncKHR returned 0x{r:04X}")


class DmaBufTarget:
    """One dma-heap buffer imported as a LINEAR renderable EGLImage + FBO.

    `view` is a read-only (h, w, 4) uint8 numpy array over the cached mmap.
    Bracket every CPU read with begin_read()/end_read() (cache invalidate /
    release); render into `fbo` like any other framebuffer.
    """

    def __init__(self, width, height, heap="system"):
        fns = _ext_fns()
        self._display = egl.eglGetCurrentDisplay()
        self.w, self.h = width, height
        self.pitch = width * 4
        size = (self.pitch * height + 4095) & ~4095
        self.fd = heap_alloc(size, heap)
        self.mm = mmap.mmap(self.fd, size, mmap.MAP_SHARED, mmap.PROT_READ)
        self.view = np.frombuffer(self.mm, np.uint8,
                                  self.pitch * height).reshape(height, width, 4)

        attribs = (c_int * 17)(
            egl.EGL_WIDTH, width,
            egl.EGL_HEIGHT, height,
            egl.EGL_LINUX_DRM_FOURCC_EXT, FOURCC_ABGR8888,
            egl.EGL_DMA_BUF_PLANE0_FD_EXT, self.fd,
            egl.EGL_DMA_BUF_PLANE0_OFFSET_EXT, 0,
            egl.EGL_DMA_BUF_PLANE0_PITCH_EXT, self.pitch,
            egl.EGL_DMA_BUF_PLANE0_MODIFIER_LO_EXT, DRM_FORMAT_MOD_LINEAR,
            egl.EGL_DMA_BUF_PLANE0_MODIFIER_HI_EXT, 0,
            egl.EGL_NONE)
        self.image = fns["create_image"](self._display, None,
                                         egl.EGL_LINUX_DMA_BUF_EXT, None,
                                         attribs)
        if not self.image:
            err = egl.eglGetError()
            self._close_buffer()
            raise egl.GLError(
                f"eglCreateImageKHR failed (0x{err:04X}) for {width}x{height} "
                f"LINEAR ABGR8888 from dma-heap '{heap}'")

        rbo = c_uint(0)
        egl.glGenRenderbuffers(1, byref(rbo))
        self.rbo = rbo.value
        egl.glBindRenderbuffer(egl.GL_RENDERBUFFER, self.rbo)
        fns["rbo_storage"](egl.GL_RENDERBUFFER, self.image)
        egl.check_gl("EGLImage -> renderbuffer")

        fbo = c_uint(0)
        egl.glGenFramebuffers(1, byref(fbo))
        self.fbo = fbo.value
        egl.glBindFramebuffer(egl.GL_FRAMEBUFFER, self.fbo)
        egl.glFramebufferRenderbuffer(egl.GL_FRAMEBUFFER,
                                      egl.GL_COLOR_ATTACHMENT0,
                                      egl.GL_RENDERBUFFER, self.rbo)
        status = egl.glCheckFramebufferStatus(egl.GL_FRAMEBUFFER)
        if status != egl.GL_FRAMEBUFFER_COMPLETE:
            self.destroy()
            raise egl.GLError(f"dmabuf FBO incomplete: 0x{status:04X}")

    def begin_read(self):
        _dmabuf_sync(self.fd, DMA_BUF_SYNC_START | DMA_BUF_SYNC_READ)

    def end_read(self):
        _dmabuf_sync(self.fd, DMA_BUF_SYNC_END | DMA_BUF_SYNC_READ)

    def _close_buffer(self):
        del self.view
        self.mm.close()
        os.close(self.fd)

    def destroy(self):
        egl.delete_framebuffer(self.fbo)
        if self.rbo:
            egl.glDeleteRenderbuffers(1, byref(c_uint(self.rbo)))
        if self.image:
            _ext_fns()["destroy_image"](self._display, self.image)
        self._close_buffer()
