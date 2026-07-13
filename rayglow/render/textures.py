"""Channel sources: image files, procedural noise, milk-feed audio.

Channel spec grammar (--channelN flags and in-file directives):
    audio              512x2 Shadertoy-style audio texture (see AudioChannel)
    milk               13x1 float texture of the milk packet's auto-gained
                       band scalars plus Pi-side derived signals (d/dt,
                       envelope, integrated phase) and the v2 feed's scalar
                       features (spectral descriptors, beat/tempo, stereo,
                       chroma) — the dynamic-range tool the audio texture's
                       clamped spectrum can't be (see MilkChannel for the map)
    spectrum           512x1 float texture: the v2 feed's real spectrum on a
                       hybrid lin/log axis (30Hz..16kHz, dB-norm 0..1), full
                       dynamic range — see SpectrumChannel.  Zero on a v0/v1 sender.
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

    Fed per frame from the milk waveform (128 samples on v0/v1, 512 on v2 —
    the FFT helpers rebuild on the first frame of a new length); the spectrum
    row is a Web-Audio-style dB-scaled rFFT of that window upsampled to 512
    (coarse — for a real spectrum use the 'spectrum' channel), with the
    analyser's 0.8 magnitude smoothing.

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
        self._buf = np.zeros((2, AUDIO_WIDTH, 4), np.uint8)
        self._buf[..., 3] = 255
        self._n = 0
        self._rebuild(128)                        # v1 default; grows to 512 on v2

    def _rebuild(self, n):
        """(Re)build the FFT helpers for an n-sample waveform window.  The
        wire's waveform length changed between protocol versions (128 -> 512),
        so size off whatever FeatureState hands us rather than hardcoding it."""
        self._n = n
        self._window = np.hanning(n).astype(np.float32)
        self._norm = self._window.sum() / 2.0     # full-scale sine -> mag 1.0
        self._smoothed = np.zeros(n // 2 + 1, np.float32)
        self._xs = np.linspace(0.0, n // 2, AUDIO_WIDTH, dtype=np.float32)
        self._xw = np.linspace(0.0, n - 1, AUDIO_WIDTH, dtype=np.float32)
        self._bins = np.arange(n // 2 + 1, dtype=np.float32)
        self._samples = np.arange(n, dtype=np.float32)

    def update(self, features):
        """features: milk FeatureState (packet values or synth fallback)."""
        wave = features.wave
        if len(wave) != self._n:
            self._rebuild(len(wave))
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
    """The milk packet's band scalars as a 13x1 RGBA32F texture — the
    dynamic-range counterpart to the audio texture's clamped spectrum —
    plus Pi-side derived signals (d/dt, envelope, integrated phase) and the
    v2 feed's scalar features (spectral descriptors, beat/tempo, stereo,
    chroma) so most audio-reactive shaders don't need a buffer file at all.

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
        texelFetch(iChannelN, ivec2(6, 0), 0)  — feed metadata
          .x = theta for sub
          .y = pkt_age  seconds since the last real UDP packet (1e6 = never)
          .z = live     1.0 = real packets, 0.0 = synth fallback — gate on
                       this to fade to an ambient mode when music stops
          .w = source_domain  0 audio, 1 sdr, 2 telemetry, … (flags bits 0-3)

    v2 feed texels (zero with a v0/v1 sender):
        texelFetch(iChannelN, ivec2(7, 0), 0)  — spectral descriptors
          .x = centroid  brightness, 0..1 of Nyquist
          .y = flux      onset strength, auto-gained (1.0 = typical)
          .z = flatness  0 tonal .. 1 noise-like
          .w = rolloff   85%-energy frequency, 0..1 of Nyquist
        texelFetch(iChannelN, ivec2(8, 0), 0)  — dynamics + tempo
          .x = crest     spectral peak/mean (peakiness)
          .y = bpm/240   tempo, normalized — multiply by 240 for BPM
          .z = beat_phase  0..1, ramps each beat (0 = on the beat)
          .w = beat_conf   tempo-lock confidence 0..1
        texelFetch(iChannelN, ivec2(9, 0), 0)  — beat pulses + stereo
          .x = beat      1.0 on the frame of a beat onset, else 0
          .y = downbeat  1.0 on every 4th beat
          .z = width     L/R correlation: -1 anti-phase, 0 wide, +1 mono
          .w = pan       -1 hard left .. +1 hard right
        texelFetch(iChannelN, ivec2(10..12, 0), 0)  — chroma (pitch classes),
                       peak-normalized 0..1, three texels of four:
            texel 10 .xyzw = C  C# D  D#
            texel 11 .xyzw = E  F  F# G
            texel 12 .xyzw = G# A  A# B

    CAUTION on 'bass': it's MilkDrop's band, 0-4kHz with the lowest bins
    equalized away — it tracks the low-mid mix, not the subwoofer.  'sub'
    (protocol v1) is the true 23-117Hz band; with a v0 sender it falls back
    to bass.
    """

    feed_driven = True
    WIDTH = 13          # texels: 0-4 bands, 5-6 derived/meta, 7-9 v2 scalars, 10-12 chroma

    # Derived-signal time constants, 1/seconds (first-order lag rates).
    DDT_LAG = 25.0      # derivative slew (~40ms) — fast enough for onsets
    ENV_LAG = 8.0       # envelope chase (~125ms) — will-helix's AMP_LAG feel
    THETA_WRAP = 628.3185307179586   # 200*pi (see docstring)

    def __init__(self):
        tex = make_texture(self.WIDTH, 1, bytes(self.WIDTH * 1 * 16),
                           filt=GL_NEAREST, wrap=GL_CLAMP_TO_EDGE,
                           internal=GL_RGBA32F, data_type=GL_FLOAT)
        super().__init__("milk", tex, self.WIDTH, 1)
        egl.check_gl("milk channel")
        self._buf = np.zeros((1, self.WIDTH, 4), np.float32)
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
        b[6, 3] = float(f.source_domain)
        # v2 scalars (zero with a v0/v1 sender — FeatureState defaults)
        b[7, :] = (f.centroid, f.flux, f.flatness, f.rolloff)
        b[8, :] = (f.crest, f.bpm / 240.0, f.beat_phase, f.beat_conf)
        b[9, :] = (1.0 if f.beat else 0.0, 1.0 if f.downbeat else 0.0,
                   f.width, f.pan)
        b[10:13, :].flat = np.asarray(f.chroma, np.float32)[:12]   # 3 texels of 4
        egl.glBindTexture(GL_TEXTURE_2D, self.texture)
        egl.glTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, self.WIDTH, 1,
                            GL_RGBA, GL_FLOAT,
                            self._buf.ctypes.data_as(ctypes.c_void_p))


class SpectrumChannel(Channel):
    """The v2 feed's real spectrum as a 512x1 RGBA32F texture.

    Unlike the 'audio' texture's spectrum row — a clamped 8-bit rFFT of the
    128/512-sample waveform, faithful to shadertoy.com and heavily compressed
    (everything >-30dB pins to 1.0) — this is the sender's dedicated 4096-pt
    FFT: 512 bins from 30Hz..16kHz, dB-normalized to 0..1 with a generous music
    range, full float dynamic range, no waveform round-trip.  The x axis is a
    HYBRID linear+log scale: the first ~162 bins are linear at one FFT bin each
    (to ~1.9kHz), the rest log to 16kHz — so every bin carries real FFT data
    rather than interpolated holes in the low end (the 11.7 Hz FFT resolution is
    coarser than pure-log bands would be down there).  Remap to all-log in the
    shader if you want that look; the wire stays max-entropy.

    Sample it like any 1-D LUT (linear filtered, so a normalized x works):
        float m = texture(iChannelN, vec2(x, 0.5)).x;   // x: 0=30Hz, 1=16kHz
    or fetch an exact bin with texelFetch(iChannelN, ivec2(i, 0), 0).x.

    Zero with a v0/v1 sender (the old wire carried no real spectrum); gate on
    the milk texture's live/source flags if you need to know.
    """

    feed_driven = True
    WIDTH = 512

    def __init__(self):
        tex = make_texture(self.WIDTH, 1, bytes(self.WIDTH * 1 * 16),
                           filt=GL_LINEAR, wrap=GL_CLAMP_TO_EDGE,
                           internal=GL_RGBA32F, data_type=GL_FLOAT)
        super().__init__("spectrum", tex, self.WIDTH, 1)
        egl.check_gl("spectrum channel")
        self._buf = np.zeros((1, self.WIDTH, 4), np.float32)

    def update(self, features):
        spec = np.asarray(features.spec, np.float32)
        n = min(len(spec), self.WIDTH)
        self._buf[0, :n, 0] = spec[:n]             # magnitude in .x
        egl.glBindTexture(GL_TEXTURE_2D, self.texture)
        egl.glTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, self.WIDTH, 1,
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
    if parts[0] == "spectrum":
        return SpectrumChannel()
    if parts[0] == "noise":
        seed = int(parts[1]) if len(parts) > 1 else 0
        size = int(parts[2]) if len(parts) > 2 else NOISE_SIZE
        return noise_channel(seed, size)
    return image_channel(spec, base_dir)
