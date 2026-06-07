"""Pass + Channel: one compiled shader program rendering into one FBO.

The 'image' pass renders single-buffered RGBA8 (it gets read back every
frame).  Buffer passes (v3 multipass, Shadertoy Buffer A-D) render into a
float ping-pong texture pair: each frame draws into the back texture while
readers bind the front, so a pass reading *itself* sees the previous frame
and a pass reading an earlier pass in the A->B->C->D->Image order sees this
frame's fresh output — shadertoy.com semantics.
"""
from ctypes import byref, c_float, c_uint

from . import egl
from . import preamble
from .egl import (GL_CLAMP_TO_EDGE, GL_COLOR_ATTACHMENT0, GL_EXTENSIONS,
                  GL_FLOAT, GL_FRAGMENT_SHADER, GL_FRAMEBUFFER,
                  GL_FRAMEBUFFER_COMPLETE, GL_HALF_FLOAT, GL_LINEAR,
                  GL_NEAREST, GL_REPEAT, GL_RGBA, GL_RGBA8, GL_RGBA16F,
                  GL_RGBA32F, GL_TEXTURE0, GL_TEXTURE_2D,
                  GL_TEXTURE_MAG_FILTER, GL_TEXTURE_MIN_FILTER,
                  GL_TEXTURE_WRAP_S, GL_TEXTURE_WRAP_T, GL_TRIANGLES,
                  GL_UNSIGNED_BYTE, GL_VERTEX_SHADER)

_PIXEL_BYTES = {GL_UNSIGNED_BYTE: 4, GL_HALF_FLOAT: 8, GL_FLOAT: 16}

# Every uniform the preamble declares; locations cached per-link (-1 = the
# compiler optimized it out — skipped at set time).  Arrays are looked up
# via their [0] element and set with glUniform*fv, count 4.
_UNIFORMS = ("iResolution", "iTime", "iTimeDelta", "iFrameRate",
             "iSampleRate", "iFrame", "iMouse", "iDate",
             "iChannel0", "iChannel1", "iChannel2", "iChannel3",
             "iChannelTime[0]", "iChannelResolution[0]")


def make_texture(width, height, data=None, filt=GL_LINEAR, wrap=GL_REPEAT,
                 internal=GL_RGBA8, data_type=GL_UNSIGNED_BYTE):
    """Allocate a texture; data is bytes/None."""
    tex = c_uint(0)
    egl.glGenTextures(1, byref(tex))
    egl.glBindTexture(GL_TEXTURE_2D, tex.value)
    egl.glTexImage2D(GL_TEXTURE_2D, 0, internal, width, height, 0,
                     GL_RGBA, data_type, data)
    egl.glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, filt)
    egl.glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, filt)
    egl.glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, wrap)
    egl.glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, wrap)
    return tex.value


def make_render_target(width, height, internal=GL_RGBA8,
                       data_type=GL_UNSIGNED_BYTE, data=None,
                       filt=GL_NEAREST):
    """Texture + FBO rendering into it.  Returns (fbo, tex).
    Pass zeroed `data` for buffer textures — glTexImage2D(None) is
    uninitialized memory, and Shadertoy buffers must start black."""
    tex = make_texture(width, height, data, filt=filt, wrap=GL_CLAMP_TO_EDGE,
                       internal=internal, data_type=data_type)
    fbo = c_uint(0)
    egl.glGenFramebuffers(1, byref(fbo))
    egl.glBindFramebuffer(GL_FRAMEBUFFER, fbo.value)
    egl.glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
                               GL_TEXTURE_2D, tex, 0)
    status = egl.glCheckFramebufferStatus(GL_FRAMEBUFFER)
    if status != GL_FRAMEBUFFER_COMPLETE:
        raise egl.GLError(f"FBO incomplete: 0x{status:04X}")
    return fbo.value, tex


def pick_buffer_format():
    """Best float render-target format the driver offers, for buffer passes.
    Shadertoy buffers are RGBA32F; sims misbehave in 8-bit.  Returns
    (internal, data_type, filter, label).  32F is only linearly filterable
    with OES_texture_float_linear — otherwise nearest (texelFetch-style
    sims don't care; soft-blur feedback shaders look slightly crisper)."""
    exts = (egl.glGetString(GL_EXTENSIONS) or b"").decode()
    if "GL_EXT_color_buffer_float" in exts:
        filt = (GL_LINEAR if "GL_OES_texture_float_linear" in exts
                else GL_NEAREST)
        return GL_RGBA32F, GL_FLOAT, filt, "RGBA32F"
    if "GL_EXT_color_buffer_half_float" in exts:
        return GL_RGBA16F, GL_HALF_FLOAT, GL_LINEAR, "RGBA16F"
    return GL_RGBA8, GL_UNSIGNED_BYTE, GL_LINEAR, \
        "RGBA8 — no float render support; stateful sims will misbehave"


class Channel:
    """One iChannelN binding.  Kinds: 'none' (1x1 black dummy), the
    'texture'/'noise'/'audio' built in textures.py, and 'buffer' below."""

    def __init__(self, kind="none", texture=0, width=1, height=1):
        self.kind = kind
        self.texture = texture
        self.width = width
        self.height = height

    def resolve_texture(self, frame_ctx=None):
        """GL texture id to bind this frame.  frame_ctx (multipass) is the
        pass registry, for resolving buffer passes' front textures."""
        return self.texture


class BufferChannel(Channel):
    """Reads another pass's most recently completed frame (or this pass's
    own previous frame, when a buffer reads itself)."""

    def __init__(self, registry, name):
        super().__init__("buffer")
        self._registry = registry
        self._name = name

    def resolve_texture(self, frame_ctx=None):
        p = self._registry[self._name]
        self.width, self.height = p.width, p.height
        return p.front_tex


class Pass:
    """One shader program + its output render target.

    double_buffered (buffer passes): two render targets, ping-ponged each
    frame.  `fbo`/`out_tex` always point at what render() will draw into
    next; `front_tex` is what readers should sample.
    """

    def __init__(self, name, width, height, dummy_texture,
                 double_buffered=False, internal=GL_RGBA8,
                 data_type=GL_UNSIGNED_BYTE, filt=GL_NEAREST):
        self.name = name
        self.width = width
        self.height = height
        self.program = 0                       # last-known-good program
        self._locs = {}
        self.channels = [Channel(texture=dummy_texture) for _ in range(4)]
        self.double_buffered = double_buffered
        if double_buffered:
            zero = bytes(width * height * _PIXEL_BYTES[data_type])
            self._targets = [
                make_render_target(width, height, internal, data_type,
                                   zero, filt)
                for _ in range(2)]
            self._back = 0
            self.fbo, self.out_tex = self._targets[0]
        else:
            self.fbo, self.out_tex = make_render_target(width, height)
        egl.check_gl(f"pass '{name}' render target")

    @property
    def front_tex(self):
        """Most recently completed frame (zero-initialized before frame 0)."""
        if not self.double_buffered:
            return self.out_tex
        return self._targets[1 - self._back][1]

    def compile(self, user_src):
        """(Re)compile from Shadertoy source.  On failure the previous good
        program stays active.  Returns (ok, message) — message is the
        remapped info log on failure, or warning text (may be '') on success.
        """
        full_src, warnings = preamble.assemble(user_src)
        vs, log = egl.compile_shader(GL_VERTEX_SHADER, preamble.VERTEX_SHADER)
        if not vs:
            return False, f"vertex shader (internal!): {log}"
        fs, log = egl.compile_shader(GL_FRAGMENT_SHADER, full_src)
        if not fs:
            egl.glDeleteShader(vs)
            return False, preamble.remap_log(log)
        prog, log = egl.link_program(vs, fs)
        if not prog:
            return False, preamble.remap_log(log)
        if self.program:
            egl.glDeleteProgram(self.program)
        self.program = prog
        self._locs = {n: egl.glGetUniformLocation(prog, n.encode())
                      for n in _UNIFORMS}
        return True, "\n".join(warnings)

    # -- per-frame ----------------------------------------------------------
    def _set(self, name, setter, *vals):
        loc = self._locs.get(name, -1)
        if loc != -1:
            setter(loc, *vals)

    def render(self, state, frame_ctx=None):
        """Draw the fullscreen triangle with this pass's program.

        state: object with attributes time, dt, frame, frame_rate,
        mouse (4-tuple), date (4-tuple).  Resolution is this pass's own.
        """
        if self.double_buffered:
            self.fbo, self.out_tex = self._targets[self._back]
        egl.glBindFramebuffer(GL_FRAMEBUFFER, self.fbo)
        egl.glViewport(0, 0, self.width, self.height)
        egl.glUseProgram(self.program)

        self._set("iResolution", egl.glUniform3f,
                  float(self.width), float(self.height), 1.0)
        self._set("iTime", egl.glUniform1f, state.time)
        self._set("iTimeDelta", egl.glUniform1f, state.dt)
        self._set("iFrameRate", egl.glUniform1f, state.frame_rate)
        self._set("iSampleRate", egl.glUniform1f, 44100.0)
        self._set("iFrame", egl.glUniform1i, state.frame)
        self._set("iMouse", egl.glUniform4f, *state.mouse)
        self._set("iDate", egl.glUniform4f, *state.date)

        t = state.time
        self._set("iChannelTime[0]", egl.glUniform1fv, 4,
                  (c_float * 4)(t, t, t, t))
        res = (c_float * 12)()
        for i, ch in enumerate(self.channels):
            egl.glActiveTexture(GL_TEXTURE0 + i)
            # resolve first: buffer channels update width/height here
            egl.glBindTexture(GL_TEXTURE_2D, ch.resolve_texture(frame_ctx))
            res[3 * i], res[3 * i + 1], res[3 * i + 2] = \
                float(ch.width), float(ch.height), 1.0
            self._set(f"iChannel{i}", egl.glUniform1i, i)
        self._set("iChannelResolution[0]", egl.glUniform3fv, 4, res)

        egl.glDrawArrays(GL_TRIANGLES, 0, 3)
        if self.double_buffered:
            self._back ^= 1                    # fresh frame becomes front
