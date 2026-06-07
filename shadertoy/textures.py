"""Channel sources: image files, procedural noise, milk-feed audio.

Channel spec grammar (--channelN flags and in-file directives):
    audio              512x2 Shadertoy-style audio texture (see AudioChannel)
    milk               8x1 float texture of the milk packet's auto-gained
                       band scalars plus Pi-side derived signals (d/dt,
                       envelope, integrated phase) — the dynamic-range tool
                       the audio texture's clamped spectrum can't be
                       (see MilkChannel for the texel map)
    noise[:seed[:size]]  RGBA white noise, default seed 0 / 256x256 — a
                       stand-in for shadertoy.com's noise textures (same
                       idea, different exact values; shaders that hash off
                       noise look right, ones keyed to exact texels differ)
    bufA..bufD, self   another pass's previous output (multipass; directive
                       only — resolved in pipeline.py, not here)
    <path>             image file via PIL; vertically flipped on upload to
                       match Shadertoy's default vflip (uv 0,0 bottom-left)

Shadertoy stores channel bindings in site metadata, not GLSL — so when
porting a multipass shader, declare them as comments in each pass file:

    // iChannel0: self
    // iChannel1: audio

Requires a current GL context (create after GLContext()).
"""
import ctypes
import os
import re

import numpy as np

from . import egl
from .egl import (GL_CLAMP_TO_EDGE, GL_FLOAT, GL_LINEAR, GL_NEAREST,
                  GL_RGBA, GL_RGBA32F, GL_TEXTURE_2D, GL_UNSIGNED_BYTE)
from .passes import Channel, make_texture

NOISE_SIZE = 256
AUDIO_WIDTH = 512   # shadertoy.com audio texture width


_DIRECTIVE = re.compile(r"^\s*//\s*iChannel([0-3])\s*[:=]\s*(.+?)\s*$",
                        re.MULTILINE)


def parse_directives(src):
    """Extract `// iChannelN: spec` comment directives -> {index: spec}."""
    return {int(m.group(1)): m.group(2) for m in _DIRECTIVE.finditer(src)}


def image_channel(path, base_dir=None):
    """Load an image file as an iChannel texture (linear filter, repeat)."""
    from PIL import Image
    if base_dir and not os.path.isabs(path) and not os.path.exists(path):
        path = os.path.join(base_dir, path)   # directive paths: try .glsl dir
    img = Image.open(path).convert("RGBA")
    data = np.asarray(img)[::-1].tobytes()      # vflip: PIL top-down -> GL
    tex = make_texture(img.width, img.height, data)
    egl.check_gl(f"image channel {path}")
    return Channel("texture", tex, img.width, img.height)


def noise_channel(seed=0, size=NOISE_SIZE):
    """Seeded RGBA white noise, linear+repeat like Shadertoy's noise media."""
    rng = np.random.default_rng(seed)
    data = rng.integers(0, 256, (size, size, 4), dtype=np.uint8).tobytes()
    tex = make_texture(size, size, data)
    egl.check_gl("noise channel")
    return Channel("noise", tex, size, size)


class AudioChannel(Channel):
    """Shadertoy audio texture: 512x2, row y<0.5 = spectrum, y>0.5 = waveform,
    values in .x (we write greyscale RGBA).  Shaders sample it exactly like
    on shadertoy.com: texture(iChannelN, vec2(x, 0.25)).x etc.

    Fed per frame from the milk 128-sample waveform; the spectrum row is a
    Web-Audio-style dB-scaled rFFT of that window (65 bins upsampled to 512
    — coarse but the wire carries no real spectrum; see project notes),
    with the analyser's 0.8 magnitude smoothing.

    NOTE the spectrum row is faithful to shadertoy.com, which means heavily
    compressed: everything above -30dB clamps to 1.0, so bass reads pin high
    whenever music plays.  For dynamic band values use 'milk' instead.
    """

    feed_driven = True                 # AudioFeed pushes packets into us

    def __init__(self):
        tex = make_texture(AUDIO_WIDTH, 2, bytes(AUDIO_WIDTH * 2 * 4),
                           filt=GL_LINEAR, wrap=GL_CLAMP_TO_EDGE)
        super().__init__("audio", tex, AUDIO_WIDTH, 2)
        egl.check_gl("audio channel")
        n = 128                                   # milk waveform window
        self._window = np.hanning(n).astype(np.float32)
        self._norm = self._window.sum() / 2.0     # full-scale sine -> mag 1.0
        self._smoothed = np.zeros(n // 2 + 1, np.float32)
        self._buf = np.zeros((2, AUDIO_WIDTH, 4), np.uint8)
        self._buf[..., 3] = 255
        self._xs = np.linspace(0.0, n // 2, AUDIO_WIDTH, dtype=np.float32)
        self._xw = np.linspace(0.0, n - 1, AUDIO_WIDTH, dtype=np.float32)
        self._bins = np.arange(n // 2 + 1, dtype=np.float32)
        self._samples = np.arange(n, dtype=np.float32)

    def update(self, features):
        """features: milk FeatureState (packet values or synth fallback)."""
        wave = features.wave
        mag = np.abs(np.fft.rfft(wave * self._window)).astype(np.float32)
        mag /= self._norm
        self._smoothed = 0.8 * self._smoothed + 0.2 * mag
        db = 20.0 * np.log10(self._smoothed + 1e-10)
        spec = np.clip((db + 100.0) / 70.0, 0.0, 1.0)   # Web Audio -100..-30dB
        wav = np.clip(wave, -1.0, 1.0) * 0.5 + 0.5
        row0 = np.interp(self._xs, self._bins, spec)
        row1 = np.interp(self._xw, self._samples, wav)
        self._buf[0, :, :3] = (row0 * 255.0 + 0.5).astype(np.uint8)[:, None]
        self._buf[1, :, :3] = (row1 * 255.0 + 0.5).astype(np.uint8)[:, None]
        egl.glBindTexture(GL_TEXTURE_2D, self.texture)
        egl.glTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, AUDIO_WIDTH, 2,
                            GL_RGBA, GL_UNSIGNED_BYTE,
                            self._buf.ctypes.data_as(ctypes.c_void_p))


class MilkChannel(Channel):
    """The milk packet's band scalars as an 8x1 RGBA32F texture — the
    dynamic-range counterpart to the audio texture's clamped spectrum —
    plus Pi-side derived signals (d/dt, envelope, integrated phase) so most
    audio-reactive shaders don't need a buffer file at all.

    The desktop sender runs MilkDrop's auto-gain on full-resolution audio:
    each band is divided by its own long-running average, so 1.0 = "typical
    for this song right now", quiet parts dip toward ~0.5, hits spike to
    2-3.  Float texture: values above 1.0 survive.  Sample with texelFetch.

    Band texels (i = 0 bass, 1 mid, 2 treb, 3 vol, 4 sub):
        texelFetch(iChannelN, ivec2(i, 0), 0)
          .x = imm     instant value, jumps frame-to-frame (per-kick punch)
          .y = att     sender's smoothed value (slow swells; vol: .y = .x)
          .z = ddt     d/dt of imm, 1/s, lightly slewed (DDT_LAG) so the
                       packet-vs-frame beat doesn't alias.  Signed: positive
                       spike = onset/attack, negative = decay.
          .w = env     imm through a ~125ms first-order lag (ENV_LAG) — the
                       knob-free version of will-helix's amp.  Shape it in
                       the shader: mix(QUIET, LOUD, smoothstep(lo, hi, env)).
    Derived texels:
        texelFetch(iChannelN, ivec2(5, 0), 0)  .xyzw = theta for
                       bass/mid/treb/vol — integrated phase, theta += imm*dt
                       ("music time": advances ~1/s at typical level, faster
                       when loud).  Wraps at 200*pi, so sin(theta * k) is
                       seamless for k a multiple of 0.01.  For SHAPED
                       velocity (base rate + boost) you still want a bufA
                       integrator — this one's velocity is the raw band.
        texelFetch(iChannelN, ivec2(6, 0), 0)
          .x = theta for sub
          .y = pkt_age  seconds since the last real UDP packet (1e6 = never)
          .z = live     1.0 = real packets, 0.0 = synth fallback — gate on
                       this to fade to an ambient mode when music stops
        Texel 7 reserved (zero).

    CAUTION on 'bass': it's MilkDrop's band, 0-4kHz with the lowest bins
    equalized away — it tracks the low-mid mix, not the subwoofer.  'sub'
    (protocol v1) is the true 23-117Hz band; with a v0 sender it falls back
    to bass.
    """

    feed_driven = True

    # Derived-signal time constants, 1/seconds (first-order lag rates).
    DDT_LAG = 25.0      # derivative slew (~40ms) — fast enough for onsets
    ENV_LAG = 8.0       # envelope chase (~125ms) — will-helix's AMP_LAG feel
    THETA_WRAP = 628.3185307179586   # 200*pi (see docstring)

    def __init__(self):
        tex = make_texture(8, 1, bytes(8 * 1 * 16), filt=GL_NEAREST,
                           wrap=GL_CLAMP_TO_EDGE, internal=GL_RGBA32F,
                           data_type=GL_FLOAT)
        super().__init__("milk", tex, 8, 1)
        egl.check_gl("milk channel")
        self._buf = np.zeros((1, 8, 4), np.float32)
        # Band order everywhere below: bass, mid, treb, vol, sub.
        self._prev_t = None
        self._prev_imm = np.ones(5, np.float32)
        self._ddt = np.zeros(5, np.float32)
        self._env = np.ones(5, np.float32)    # start at "typical", not zero
        self._theta = np.zeros(5, np.float32)

    def update(self, features):
        f = features
        imm = np.array([f.bass, f.mid, f.treb, f.vol, f.sub], np.float32)
        att = np.array([f.bass_att, f.mid_att, f.treb_att, f.vol, f.sub_att],
                       np.float32)

        # Derived signals integrate against the engine clock carried by the
        # feature state (first frame: no dt yet, derivatives stay zero).
        dt = (f.t - self._prev_t) if self._prev_t is not None else 0.0
        self._prev_t = f.t
        if dt > 0.0:
            raw_ddt = (imm - self._prev_imm) / dt
            self._ddt += (raw_ddt - self._ddt) * min(1.0, self.DDT_LAG * dt)
            self._env += (imm - self._env) * min(1.0, self.ENV_LAG * dt)
            self._theta = (self._theta + imm * dt) % self.THETA_WRAP
        self._prev_imm = imm

        b = self._buf[0]
        b[:5, 0] = imm
        b[:5, 1] = att
        b[:5, 2] = self._ddt
        b[:5, 3] = self._env
        b[5, :] = self._theta[:4]                  # bass/mid/treb/vol phase
        b[6, 0] = self._theta[4]                   # sub phase
        b[6, 1] = min(f.pkt_age, 1e6)
        b[6, 2] = 1.0 if f.live else 0.0
        egl.glBindTexture(GL_TEXTURE_2D, self.texture)
        egl.glTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, 8, 1,
                            GL_RGBA, GL_FLOAT,
                            self._buf.ctypes.data_as(ctypes.c_void_p))


def parse_channel_spec(spec, base_dir=None):
    """Channel spec -> Channel.  See module docstring for grammar.
    Buffer specs (bufA..D/self) are pass references, not textures — the
    pipeline resolves those before calling here."""
    parts = spec.split(":")
    if parts[0] == "audio":
        return AudioChannel()
    if parts[0] == "milk":
        return MilkChannel()
    if parts[0] == "noise":
        seed = int(parts[1]) if len(parts) > 1 else 0
        size = int(parts[2]) if len(parts) > 2 else NOISE_SIZE
        return noise_channel(seed, size)
    return image_channel(spec, base_dir)
