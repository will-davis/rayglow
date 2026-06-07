"""ctypes bindings for headless EGL + OpenGL ES 3 on the Pi's V3D GPU.

Only the ~30 functions this renderer needs.  The context is *surfaceless*
(EGL_PLATFORM_SURFACELESS_MESA): no X, no GBM, no window — we render into an
FBO and glReadPixels it back.  Verified working on this Pi 4B (V3D 4.2,
Mesa 25.0.7, OpenGL ES 3.1).

Every function gets explicit argtypes/restype — ctypes inference on 64-bit
pointers is how you segfault.  Enum values are standard Khronos constants
(the EGL ones were verified live before this module was written).
"""
import ctypes
from ctypes import (POINTER, byref, c_char, c_char_p, c_float, c_int,
                    c_uint, c_void_p)

_egl = ctypes.CDLL("libEGL.so.1", mode=ctypes.RTLD_GLOBAL)
_gl = ctypes.CDLL("libGLESv2.so.2", mode=ctypes.RTLD_GLOBAL)

# ---------------------------------------------------------------------------
# EGL constants
# ---------------------------------------------------------------------------
EGL_PLATFORM_SURFACELESS_MESA = 0x31DD
EGL_OPENGL_ES_API = 0x30A0
EGL_CONTEXT_CLIENT_VERSION = 0x3098
EGL_NONE = 0x3038
EGL_SUCCESS = 0x3000
EGL_NO_SURFACE = None
EGL_NO_CONFIG = None  # EGL_KHR_no_config_context

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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
class GLError(RuntimeError):
    pass


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
    """Headless surfaceless EGL context, OpenGL ES 3, current on creation."""

    def __init__(self):
        self.display = eglGetPlatformDisplay(
            EGL_PLATFORM_SURFACELESS_MESA, None, None)
        if not self.display:
            raise GLError(f"eglGetPlatformDisplay failed "
                          f"(0x{eglGetError():04X}) — is /dev/dri readable?")
        major, minor = c_int(0), c_int(0)
        if not eglInitialize(self.display, byref(major), byref(minor)):
            raise GLError(f"eglInitialize failed (0x{eglGetError():04X})")
        if not eglBindAPI(EGL_OPENGL_ES_API):
            raise GLError(f"eglBindAPI failed (0x{eglGetError():04X})")
        # Surfaceless has no surface configs; EGL_KHR_no_config_context lets
        # us pass a NULL config (verified working on Mesa 25 / V3D).
        attribs = (c_int * 3)(EGL_CONTEXT_CLIENT_VERSION, 3, EGL_NONE)
        self.context = eglCreateContext(
            self.display, EGL_NO_CONFIG, None, attribs)
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

    def info(self):
        def s(enum):
            v = glGetString(enum)
            return v.decode() if v else "?"
        return (f"{s(GL_RENDERER)} | {s(GL_VERSION)} | "
                f"GLSL {s(GL_SHADING_LANGUAGE_VERSION)}")

    def destroy(self):
        if self.display:
            eglMakeCurrent(self.display, EGL_NO_SURFACE, EGL_NO_SURFACE, None)
            eglTerminate(self.display)
            self.display = None
