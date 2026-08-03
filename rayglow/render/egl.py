"""ctypes bindings for headless EGL + OpenGL ES 3.

Only the ~30 functions this renderer needs.  The context is headless: no X,
no GBM, no window — we render into an FBO and read it back.  Two ways to get
a display (GLContext picks at runtime, see its docstring):

  surfaceless — EGL_PLATFORM_SURFACELESS_MESA, the original Mesa-only path.
                Originally brought up on a Pi 4B (V3D 4.2); now runs on the
                Pi 5 (V3D 7.1, Mesa, GLES 3.1) and any desktop Mesa EGL.
  device      — EGL_EXT_platform_device: enumerate GPUs and take a display
                straight off one.  The headless route on NVIDIA's proprietary
                driver, which does not implement the Mesa platform
                (remote render on ubuntu-server's RTX 4080).

Every function gets explicit argtypes/restype — ctypes inference on 64-bit
pointers is how you segfault.  Enum values are standard Khronos constants
(the EGL ones were verified live before this module was written).
"""
import ctypes
import os
import sys
from ctypes import (POINTER, byref, c_char, c_char_p, c_float, c_int,
                    c_ssize_t, c_ubyte, c_uint, c_void_p)

_egl = ctypes.CDLL("libEGL.so.1", mode=ctypes.RTLD_GLOBAL)
_gl = ctypes.CDLL("libGLESv2.so.2", mode=ctypes.RTLD_GLOBAL)

# ---------------------------------------------------------------------------
# EGL constants
# ---------------------------------------------------------------------------
EGL_PLATFORM_SURFACELESS_MESA = 0x31DD
EGL_PLATFORM_DEVICE_EXT = 0x313F   # EGL_EXT_platform_device
EGL_EXTENSIONS = 0x3055            # eglQueryDeviceStringEXT: device extensions
EGL_OPENGL_ES_API = 0x30A0
EGL_CONTEXT_CLIENT_VERSION = 0x3098
EGL_NONE = 0x3038
EGL_SUCCESS = 0x3000
EGL_NO_SURFACE = None
EGL_NO_CONFIG = None  # EGL_KHR_no_config_context

# eglChooseConfig attributes — only used on the device path when the driver
# lacks EGL_KHR_no_config_context (we still never create a surface).
EGL_SURFACE_TYPE = 0x3033
EGL_PBUFFER_BIT = 0x0001
EGL_RENDERABLE_TYPE = 0x3040
EGL_OPENGL_ES3_BIT = 0x0040
EGL_RED_SIZE = 0x3024
EGL_GREEN_SIZE = 0x3023
EGL_BLUE_SIZE = 0x3022

# ---------------------------------------------------------------------------
# GL constants (Khronos GLES3 standard values)
# ---------------------------------------------------------------------------
GL_VERTEX_SHADER = 0x8B31
GL_FRAGMENT_SHADER = 0x8B30
GL_COMPILE_STATUS = 0x8B81
GL_LINK_STATUS = 0x8B82
GL_INFO_LOG_LENGTH = 0x8B84

GL_FRAMEBUFFER = 0x8D40
GL_COLOR_ATTACHMENT0 = 0x8CE0
GL_FRAMEBUFFER_COMPLETE = 0x8CD5

GL_TEXTURE_2D = 0x0DE1
GL_TEXTURE0 = 0x84C0
GL_RGBA = 0x1908
GL_RGBA8 = 0x8058
GL_RGBA16F = 0x881A
GL_RGBA32F = 0x8814
GL_UNSIGNED_BYTE = 0x1401
GL_HALF_FLOAT = 0x140B
GL_FLOAT = 0x1406
GL_TEXTURE_MIN_FILTER = 0x2801
GL_TEXTURE_MAG_FILTER = 0x2800
GL_TEXTURE_WRAP_S = 0x2802
GL_TEXTURE_WRAP_T = 0x2803
GL_LINEAR = 0x2601
GL_NEAREST = 0x2600
GL_REPEAT = 0x2901
GL_CLAMP_TO_EDGE = 0x812F

GL_TRIANGLES = 0x0004
GL_COLOR_BUFFER_BIT = 0x4000
GL_NO_ERROR = 0
GL_VENDOR = 0x1F00
GL_RENDERER = 0x1F01
GL_VERSION = 0x1F02
GL_EXTENSIONS = 0x1F03
GL_SHADING_LANGUAGE_VERSION = 0x8B8C

# Pixel buffer objects — async glReadPixels (GPU->CPU DMA without a CPU stall).
GL_PIXEL_PACK_BUFFER = 0x88EB
GL_STREAM_READ = 0x88E1
GL_MAP_READ_BIT = 0x0001

GL_RENDERBUFFER = 0x8D41

# EGL_EXT_image_dma_buf_import — import a dma-buf as an EGLImage (dmabuf.py).
EGL_LINUX_DMA_BUF_EXT = 0x3270
EGL_LINUX_DRM_FOURCC_EXT = 0x3271
EGL_DMA_BUF_PLANE0_FD_EXT = 0x3272
EGL_DMA_BUF_PLANE0_OFFSET_EXT = 0x3273
EGL_DMA_BUF_PLANE0_PITCH_EXT = 0x3274
EGL_DMA_BUF_PLANE0_MODIFIER_LO_EXT = 0x3443
EGL_DMA_BUF_PLANE0_MODIFIER_HI_EXT = 0x3444
EGL_WIDTH = 0x3057
EGL_HEIGHT = 0x3056

# EGL_KHR_fence_sync — CPU-side wait for queued GPU work (dmabuf.Fence).
EGL_SYNC_FENCE_KHR = 0x30F9
EGL_SYNC_FLUSH_COMMANDS_BIT_KHR = 0x0001
EGL_FOREVER_KHR = 0xFFFFFFFFFFFFFFFF
EGL_CONDITION_SATISFIED_KHR = 0x30F6

# ---------------------------------------------------------------------------
# Function signatures
# ---------------------------------------------------------------------------
def _bind(lib, name, restype, argtypes):
    fn = getattr(lib, name)
    fn.restype = restype
    fn.argtypes = argtypes
    return fn

# EGL
eglGetPlatformDisplay = _bind(_egl, "eglGetPlatformDisplay", c_void_p,
                              [c_uint, c_void_p, c_void_p])
eglInitialize = _bind(_egl, "eglInitialize", c_uint,
                      [c_void_p, POINTER(c_int), POINTER(c_int)])
eglChooseConfig = _bind(_egl, "eglChooseConfig", c_uint,
                        [c_void_p, POINTER(c_int), POINTER(c_void_p), c_int,
                         POINTER(c_int)])
eglBindAPI = _bind(_egl, "eglBindAPI", c_uint, [c_uint])
eglCreateContext = _bind(_egl, "eglCreateContext", c_void_p,
                         [c_void_p, c_void_p, c_void_p, POINTER(c_int)])
eglMakeCurrent = _bind(_egl, "eglMakeCurrent", c_uint,
                       [c_void_p, c_void_p, c_void_p, c_void_p])
eglGetError = _bind(_egl, "eglGetError", c_int, [])
eglTerminate = _bind(_egl, "eglTerminate", c_uint, [c_void_p])

# GL — shaders/programs
glCreateShader = _bind(_gl, "glCreateShader", c_uint, [c_uint])
glShaderSource = _bind(_gl, "glShaderSource", None,
                       [c_uint, c_int, POINTER(c_char_p), POINTER(c_int)])
glCompileShader = _bind(_gl, "glCompileShader", None, [c_uint])
glGetShaderiv = _bind(_gl, "glGetShaderiv", None,
                      [c_uint, c_uint, POINTER(c_int)])
glGetShaderInfoLog = _bind(_gl, "glGetShaderInfoLog", None,
                           [c_uint, c_int, POINTER(c_int), POINTER(c_char)])
glDeleteShader = _bind(_gl, "glDeleteShader", None, [c_uint])
glCreateProgram = _bind(_gl, "glCreateProgram", c_uint, [])
glAttachShader = _bind(_gl, "glAttachShader", None, [c_uint, c_uint])
glLinkProgram = _bind(_gl, "glLinkProgram", None, [c_uint])
glGetProgramiv = _bind(_gl, "glGetProgramiv", None,
                       [c_uint, c_uint, POINTER(c_int)])
glGetProgramInfoLog = _bind(_gl, "glGetProgramInfoLog", None,
                            [c_uint, c_int, POINTER(c_int), POINTER(c_char)])
glDeleteProgram = _bind(_gl, "glDeleteProgram", None, [c_uint])
glUseProgram = _bind(_gl, "glUseProgram", None, [c_uint])
glGetUniformLocation = _bind(_gl, "glGetUniformLocation", c_int,
                             [c_uint, c_char_p])

# GL — uniforms
glUniform1f = _bind(_gl, "glUniform1f", None, [c_int, c_float])
glUniform1i = _bind(_gl, "glUniform1i", None, [c_int, c_int])
glUniform2f = _bind(_gl, "glUniform2f", None, [c_int, c_float, c_float])
glUniform3f = _bind(_gl, "glUniform3f", None,
                    [c_int, c_float, c_float, c_float])
glUniform4f = _bind(_gl, "glUniform4f", None,
                    [c_int, c_float, c_float, c_float, c_float])
glUniform1fv = _bind(_gl, "glUniform1fv", None,
                     [c_int, c_int, POINTER(c_float)])
glUniform3fv = _bind(_gl, "glUniform3fv", None,
                     [c_int, c_int, POINTER(c_float)])

# GL — VAO / draw
glGenVertexArrays = _bind(_gl, "glGenVertexArrays", None,
                          [c_int, POINTER(c_uint)])
glBindVertexArray = _bind(_gl, "glBindVertexArray", None, [c_uint])
glDrawArrays = _bind(_gl, "glDrawArrays", None, [c_uint, c_int, c_int])

# GL — FBO / textures
glGenFramebuffers = _bind(_gl, "glGenFramebuffers", None,
                          [c_int, POINTER(c_uint)])
glBindFramebuffer = _bind(_gl, "glBindFramebuffer", None, [c_uint, c_uint])
glGenTextures = _bind(_gl, "glGenTextures", None, [c_int, POINTER(c_uint)])
glDeleteTextures = _bind(_gl, "glDeleteTextures", None,
                         [c_int, POINTER(c_uint)])
glDeleteFramebuffers = _bind(_gl, "glDeleteFramebuffers", None,
                             [c_int, POINTER(c_uint)])
glBindTexture = _bind(_gl, "glBindTexture", None, [c_uint, c_uint])
glActiveTexture = _bind(_gl, "glActiveTexture", None, [c_uint])
glTexImage2D = _bind(_gl, "glTexImage2D", None,
                     [c_uint, c_int, c_int, c_int, c_int, c_int,
                      c_uint, c_uint, c_void_p])
glTexSubImage2D = _bind(_gl, "glTexSubImage2D", None,
                        [c_uint, c_int, c_int, c_int, c_int, c_int,
                         c_uint, c_uint, c_void_p])
glTexParameteri = _bind(_gl, "glTexParameteri", None, [c_uint, c_uint, c_int])
glFramebufferTexture2D = _bind(_gl, "glFramebufferTexture2D", None,
                               [c_uint, c_uint, c_uint, c_uint, c_int])
glCheckFramebufferStatus = _bind(_gl, "glCheckFramebufferStatus", c_uint,
                                 [c_uint])

# GL — frame
glViewport = _bind(_gl, "glViewport", None, [c_int, c_int, c_int, c_int])
glClearColor = _bind(_gl, "glClearColor", None,
                     [c_float, c_float, c_float, c_float])
glClear = _bind(_gl, "glClear", None, [c_uint])
glReadPixels = _bind(_gl, "glReadPixels", None,
                     [c_int, c_int, c_int, c_int, c_uint, c_uint, c_void_p])
glGetError = _bind(_gl, "glGetError", c_uint, [])
glGetString = _bind(_gl, "glGetString", c_char_p, [c_uint])

# EGL/GL extension entry points must come from eglGetProcAddress (the .so may
# not export them). dmabuf.py binds its extension functions through this.
eglGetProcAddress = _bind(_egl, "eglGetProcAddress", c_void_p, [c_char_p])
eglGetCurrentDisplay = _bind(_egl, "eglGetCurrentDisplay", c_void_p, [])


def load_ext(name, restype, argtypes):
    """Bind an EGL/GL extension function, or raise GLError if absent."""
    addr = eglGetProcAddress(name.encode())
    if not addr:
        raise GLError(f"extension function {name} not available")
    return ctypes.CFUNCTYPE(restype, *argtypes)(addr)


# GL — renderbuffers (dmabuf.py attaches an EGLImage as the render target).
glGenRenderbuffers = _bind(_gl, "glGenRenderbuffers", None,
                           [c_int, POINTER(c_uint)])
glBindRenderbuffer = _bind(_gl, "glBindRenderbuffer", None, [c_uint, c_uint])
glDeleteRenderbuffers = _bind(_gl, "glDeleteRenderbuffers", None,
                              [c_int, POINTER(c_uint)])
glFramebufferRenderbuffer = _bind(_gl, "glFramebufferRenderbuffer", None,
                                  [c_uint, c_uint, c_uint, c_uint])
glFlush = _bind(_gl, "glFlush", None, [])

# GL — pixel buffer objects (async readback). glReadPixels into a bound
# GL_PIXEL_PACK_BUFFER returns immediately (the last arg becomes a byte offset,
# pass 0); glMapBufferRange then hands back the previous frame's bytes.
glGenBuffers = _bind(_gl, "glGenBuffers", None, [c_int, POINTER(c_uint)])
glBindBuffer = _bind(_gl, "glBindBuffer", None, [c_uint, c_uint])
glBufferData = _bind(_gl, "glBufferData", None,
                     [c_uint, c_ssize_t, c_void_p, c_uint])
glMapBufferRange = _bind(_gl, "glMapBufferRange", c_void_p,
                         [c_uint, c_ssize_t, c_ssize_t, c_uint])
glUnmapBuffer = _bind(_gl, "glUnmapBuffer", c_ubyte, [c_uint])
glDeleteBuffers = _bind(_gl, "glDeleteBuffers", None, [c_int, POINTER(c_uint)])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
class GLError(RuntimeError):
    pass


def delete_texture(tex):
    """Free one texture id (no-op for 0)."""
    if tex:
        glDeleteTextures(1, byref(c_uint(tex)))


def delete_framebuffer(fbo):
    """Free one framebuffer id (no-op for 0)."""
    if fbo:
        glDeleteFramebuffers(1, byref(c_uint(fbo)))


def check_gl(where):
    """glGetError check — call during init/setup only, never per-frame
    (it forces a pipeline sync)."""
    err = glGetError()
    if err != GL_NO_ERROR:
        raise GLError(f"GL error 0x{err:04X} at {where}")


def compile_shader(kind, source):
    """Compile one shader.  Returns (shader_id, None) or (0, infolog)."""
    shader = glCreateShader(kind)
    src = c_char_p(source.encode())
    glShaderSource(shader, 1, byref(src), None)
    glCompileShader(shader)
    status = c_int(0)
    glGetShaderiv(shader, GL_COMPILE_STATUS, byref(status))
    if not status.value:
        log = _info_log(glGetShaderiv, glGetShaderInfoLog, shader)
        glDeleteShader(shader)
        return 0, log
    return shader, None


def link_program(vs, fs):
    """Link vertex+fragment shaders.  Returns (program_id, None) or (0, log).
    The shader objects are deleted either way (they're owned by the program
    once attached; flagged for deletion otherwise)."""
    prog = glCreateProgram()
    glAttachShader(prog, vs)
    glAttachShader(prog, fs)
    glLinkProgram(prog)
    glDeleteShader(vs)
    glDeleteShader(fs)
    status = c_int(0)
    glGetProgramiv(prog, GL_LINK_STATUS, byref(status))
    if not status.value:
        log = _info_log(glGetProgramiv, glGetProgramInfoLog, prog)
        glDeleteProgram(prog)
        return 0, log
    return prog, None


def _info_log(get_iv, get_log, obj):
    length = c_int(0)
    get_iv(obj, GL_INFO_LOG_LENGTH, byref(length))
    if length.value <= 1:
        return "(no info log)"
    buf = ctypes.create_string_buffer(length.value)
    get_log(obj, length.value, None, buf)
    return buf.value.decode(errors="replace").strip()


class GLContext:
    """Headless EGL context, OpenGL ES 3, current on creation.

    `platform` (default: $RAYGLOW_EGL, else "auto") picks how the display is
    obtained:

      surfaceless — EGL_PLATFORM_SURFACELESS_MESA: the original path,
                    unchanged (Pi V3D wall runs, desktop Mesa dry-runs).
      device      — EGL_EXT_platform_device: enumerate GPUs with
                    eglQueryDevicesEXT and take a display off one directly.
                    The only headless route on NVIDIA's proprietary driver.
                    $RAYGLOW_EGL_DEVICE=N picks the device index when several
                    enumerate (otherwise: CUDA-capable > DRM node > rest).
      auto        — surfaceless first (bit-for-bit the old behavior where it
                    works), then device.  Caveat: a box with BOTH Mesa and
                    NVIDIA installed answers surfaceless with llvmpipe
                    (software GL) rather than failing — force
                    RAYGLOW_EGL=device / --egl device there (a warning
                    prints when that happens).
    """

    def __init__(self, platform=None):
        platform = platform or os.environ.get("RAYGLOW_EGL") or "auto"
        if platform == "surfaceless":
            self.display, self.platform = self._surfaceless_display(), platform
        elif platform == "device":
            self.display, self.platform = self._device_display(), platform
        elif platform == "auto":
            try:
                self.display, self.platform = (self._surfaceless_display(),
                                               "surfaceless")
            except GLError:
                self.display, self.platform = self._device_display(), "device"
        else:
            raise GLError(f"unknown EGL platform {platform!r} "
                          "(auto | surfaceless | device)")
        if not eglBindAPI(EGL_OPENGL_ES_API):
            raise GLError(f"eglBindAPI failed (0x{eglGetError():04X})")
        # Headless has no surface; EGL_KHR_no_config_context lets us pass a
        # NULL config (verified working on Mesa 25 / V3D).  A driver without
        # it gets any GLES3 config instead — no surface is created either way
        # (EGL_KHR_surfaceless_context).
        attribs = (c_int * 3)(EGL_CONTEXT_CLIENT_VERSION, 3, EGL_NONE)
        self.context = eglCreateContext(
            self.display, EGL_NO_CONFIG, None, attribs)
        if not self.context:
            self.context = eglCreateContext(
                self.display, self._choose_config(self.display), None, attribs)
        if not self.context:
            raise GLError(f"eglCreateContext failed (0x{eglGetError():04X})")
        if not eglMakeCurrent(self.display, EGL_NO_SURFACE, EGL_NO_SURFACE,
                              self.context):
            raise GLError(f"eglMakeCurrent failed (0x{eglGetError():04X})")
        # One real VAO bound for the lifetime of the context (VAO 0 is legal
        # in GLES3 but a named one is free and safer across Mesa versions).
        vao = c_uint(0)
        glGenVertexArrays(1, byref(vao))
        glBindVertexArray(vao.value)
        check_gl("context init")
        renderer = glGetString(GL_RENDERER) or b""
        if self.platform == "surfaceless" and b"llvmpipe" in renderer:
            print("warning: software GL (llvmpipe) — if this box has a real "
                  "GPU behind the NVIDIA driver, run with --egl device (or "
                  "RAYGLOW_EGL=device)", file=sys.stderr)

    @staticmethod
    def _surfaceless_display():
        """The Mesa surfaceless platform, initialized."""
        display = eglGetPlatformDisplay(
            EGL_PLATFORM_SURFACELESS_MESA, None, None)
        if not display:
            raise GLError(f"eglGetPlatformDisplay failed "
                          f"(0x{eglGetError():04X}) — is /dev/dri readable?")
        major, minor = c_int(0), c_int(0)
        if not eglInitialize(display, byref(major), byref(minor)):
            raise GLError(f"eglInitialize failed (0x{eglGetError():04X})")
        return display

    @staticmethod
    def _device_display():
        """EGL_EXT_platform_device, initialized on the best-ranked GPU.

        Extension entry points come from eglGetProcAddress, which needs no
        display — that is what makes this bootstrappable.
        """
        query_devices = load_ext(
            "eglQueryDevicesEXT", c_uint,
            [c_int, POINTER(c_void_p), POINTER(c_int)])
        get_platform_display = load_ext(
            "eglGetPlatformDisplayEXT", c_void_p,
            [c_uint, c_void_p, POINTER(c_int)])
        query_device_string = load_ext(
            "eglQueryDeviceStringEXT", c_char_p, [c_void_p, c_int])

        n = c_int(0)
        if not query_devices(0, None, byref(n)) or n.value < 1:
            raise GLError("EGL device platform: no devices enumerate")
        devs = (c_void_p * n.value)()
        query_devices(n.value, devs, byref(n))

        def dev_exts(d):
            s = query_device_string(d, EGL_EXTENSIONS)
            return s.decode() if s else ""

        pick = os.environ.get("RAYGLOW_EGL_DEVICE")
        if pick is not None:
            order = [int(pick)]
        else:
            # Hardware first: a CUDA-capable device (NVIDIA) over a plain DRM
            # node over the rest (llvmpipe advertises neither).  Ties keep
            # enumeration order.
            def rank(i):
                e = dev_exts(devs[i])
                return (0 if "EGL_NV_device_cuda" in e
                        else 1 if "EGL_EXT_device_drm" in e else 2, i)
            order = sorted(range(n.value), key=rank)

        err = EGL_SUCCESS
        for i in order:
            display = get_platform_display(
                EGL_PLATFORM_DEVICE_EXT, devs[i], None)
            if not display:
                err = eglGetError()
                continue
            major, minor = c_int(0), c_int(0)
            if eglInitialize(display, byref(major), byref(minor)):
                return display
            err = eglGetError()
        raise GLError(f"EGL device platform: none of {n.value} device(s) "
                      f"would initialize (last error 0x{err:04X})")

    @staticmethod
    def _choose_config(display):
        """Any RGB888 GLES3 config — the no-config-context fallback."""
        attrs = (c_int * 11)(EGL_SURFACE_TYPE, EGL_PBUFFER_BIT,
                             EGL_RENDERABLE_TYPE, EGL_OPENGL_ES3_BIT,
                             EGL_RED_SIZE, 8, EGL_GREEN_SIZE, 8,
                             EGL_BLUE_SIZE, 8, EGL_NONE)
        cfg, num = c_void_p(), c_int(0)
        if (not eglChooseConfig(display, attrs, byref(cfg), 1, byref(num))
                or num.value < 1):
            raise GLError(f"eglChooseConfig found no GLES3 config "
                          f"(0x{eglGetError():04X})")
        return cfg

    def info(self):
        def s(enum):
            v = glGetString(enum)
            return v.decode() if v else "?"
        return (f"{s(GL_RENDERER)} | {s(GL_VERSION)} | "
                f"GLSL {s(GL_SHADING_LANGUAGE_VERSION)} | "
                f"EGL {self.platform}")

    def destroy(self):
        if self.display:
            eglMakeCurrent(self.display, EGL_NO_SURFACE, EGL_NO_SURFACE, None)
            eglTerminate(self.display)
            self.display = None
