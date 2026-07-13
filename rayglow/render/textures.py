"""Channel sources: image files, procedural noise, milk-feed audio.

Channel spec grammar (--channelN flags and in-file directives):
    audio              512x2 Shadertoy-style audio texture (see AudioChannel)
    milk               16x3 float texture of the v3 feed: 8 log-spaced bands
                       with flywheel envelopes (row 0), theta phases + onsets
                       (row 1), globals — beat/key/descriptors/stereo/chroma/
                       meta + the legacy scalars — (row 2).  The
                       dynamic-range tool the audio texture's clamped
                       spectrum can't be (see MilkChannel for the map)
    spectrum           128x1 float texture: .x = the sender's real spectrum
                       on a hybrid lin/log axis (30Hz..16kHz, dB-norm 0..1),
                       .y/.z = smooth curves through the 8 band values — see
                       SpectrumChannel.  Zero on a v0/v1 sender.
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
    """The v3 feed as a 16x3 RGBA32F texture — the dynamic-range counterpart
    to the audio texture's clamped spectrum.  Everything arrives ready-made
    from the desktop sender: envelopes and theta phases are integrated on
    ITS steady clock (the Pi just uploads — the v2-era Pi-side integrators
    are gone, and with them d/dt, whose signed spikes were jarring; per-band
    onset in row 1 .w is its useful half).

    The sender runs MilkDrop-style auto-gain on every band: each is divided
    by its own long-running average, so 1.0 = "typical for this song right
    now", quiet parts dip toward ~0.5, hits spike 2-3 (clamped at 16).
    Float texture: values above 1.0 survive.  Sample with texelFetch.

    Row 0 — levels.  Cols 0-7 = bands b0..b7, log-spaced 20Hz..16kHz
    (edges 20|60|120|250|500|1k|2.5k|6k|16k); col 8 = overall volume:
        texelFetch(iChannelN, ivec2(b, 0), 0)
          .x = imm   instant value, jumps frame-to-frame (per-kick punch)
          .y = env0  ~125ms symmetric lag — the classic env feel
          .z = env1  punchy flywheel: ~60ms attack, ~500ms decay
          .w = env2  heavy flywheel: ~150ms attack, ~2s decay
    Row 1 — motion.  Same columns:
        texelFetch(iChannelN, ivec2(b, 1), 0)
          .x = theta0  "music time" phase integrating imm — advances
                       ~1 rad/s at typical level, faster when loud
          .y = theta1  integrates env1 — velocity with punchy momentum
          .z = theta2  integrates env2 — heavy momentum, slow scene motion
          .w = onset   per-band attack strength (one-sided rectified flux,
                       auto-gained ~1.0 typical); col 8 .w unused
        Thetas wrap at 200*pi: sin(theta * k) is seamless for k a multiple
        of 0.01.  Tiers wrap independently — cross-tier phase differences
        carry no meaning.
    Row 2 — globals:
        (0,2)  .x bpm/240  .y beat_phase  .z bar_phase  .w beat_conf
               beat_phase is PREDICTIVE: it ramps 0->1 and hits 1.0 ON the
               predicted beat (so shaders can anticipate hits); bar_phase
               spans 4 beats the same way.
        (1,2)  .x beat (1.0 on the beat frame)  .y downbeat (every 4th)
               .z key_idx/12 — fract()*12 -> pitch class C..B; >= 1.0 means
               minor  .w key_conf 0..1 (gate on it: key detection is a mood
               signal, not ground truth)
        (2,2)  .x centroid  .y flux  .z flatness  .w rolloff
        (3,2)  .x crest  .y width  .z pan  .w unused
        (4..6,2)  chroma (pitch classes C..B), peak-normalized, 4 per texel
        (7,2)  .x pkt_age (s since the last real packet; 1e6 = never)
               .y live (1 = real packets, 0 = synth fallback — gate ambient
               modes on this)
               .z source_domain (0 audio, 1 sdr, 2 telemetry — flags bits 0-3)
               .w unused
        (9,2)  .x bass     .y mid     .z treb     .w vol   — LEGACY block:
        (10,2) .x bass_att .y mid_att .z treb_att .w sub     the classic
        (11,2) .x sub_att  .yzw unused                        MilkDrop scalars,
               still shipped by every sender version, so pre-v3 shaders port
               1:1.  New work should prefer the bands.
        (8,2) and (12..15, any row) are spares — future features land there.

    CAUTION on legacy 'bass': it's MilkDrop's band, 0-4kHz with the lowest
    bins equalized away — it tracks the low-mid mix, not the subwoofer.
    b0/b1 (or legacy 'sub') are the true low end.

    With a v0/v1/v2 sender the band rows sit at their neutral defaults
    (imm/env 1.0, theta 0, onset 0) and the legacy block stays fully live.
    """

    feed_driven = True
    WIDTH = 16          # cols: 0-7 bands, 8 vol, 9-15 spares/legacy (row 2)
    HEIGHT = 3          # rows: 0 levels, 1 motion, 2 globals

    def __init__(self):
        tex = make_texture(self.WIDTH, self.HEIGHT,
                           bytes(self.WIDTH * self.HEIGHT * 16),
                           filt=GL_NEAREST, wrap=GL_CLAMP_TO_EDGE,
                           internal=GL_RGBA32F, data_type=GL_FLOAT)
        super().__init__("milk", tex, self.WIDTH, self.HEIGHT)
        egl.check_gl("milk channel")
        self._buf = np.zeros((self.HEIGHT, self.WIDTH, 4), np.float32)

    def update(self, features):
        f = features
        b = self._buf
        # row 0 — levels
        b[0, :8, 0] = f.bands
        b[0, :8, 1:4] = f.band_env
        b[0, 8, :] = (f.vol_imm, f.vol_env[0], f.vol_env[1], f.vol_env[2])
        # row 1 — motion
        b[1, :8, :3] = f.band_theta
        b[1, :8, 3] = f.band_onset
        b[1, 8, :3] = f.vol_theta
        # row 2 — globals
        b[2, 0, :] = (f.bpm / 240.0, f.beat_phase, f.bar_phase, f.beat_conf)
        b[2, 1, :] = (1.0 if f.beat else 0.0, 1.0 if f.downbeat else 0.0,
                      f.key_idx / 12.0, f.key_conf)
        b[2, 2, :] = (f.centroid, f.flux, f.flatness, f.rolloff)
        b[2, 3, :3] = (f.crest, f.width, f.pan)
        b[2, 4:7, :] = np.asarray(f.chroma, np.float32)[:12].reshape(3, 4)
        b[2, 7, :3] = (min(f.pkt_age, 1e6), 1.0 if f.live else 0.0,
                       float(f.source_domain))
        # legacy block — the classic scalars every sender still ships
        b[2, 9, :] = (f.bass, f.mid, f.treb, f.vol)
        b[2, 10, :] = (f.bass_att, f.mid_att, f.treb_att, f.sub)
        b[2, 11, 0] = f.sub_att
        egl.glBindTexture(GL_TEXTURE_2D, self.texture)
        egl.glTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, self.WIDTH, self.HEIGHT,
                            GL_RGBA, GL_FLOAT,
                            self._buf.ctypes.data_as(ctypes.c_void_p))


# ---- v3 spectrum axis (mirrors sender/sender.py's _spec_split, 128 bins) -------
# Only SPEC_NLIN needs mirroring; FC and R derive from it.  The sender prints
# these constants at startup — if they ever drift, re-sync here AND in
# presets/milk-spectrum.glsl.
SPEC_OUT = 128
SPEC_FMIN, SPEC_FMAX = 30.0, 16000.0
_SPEC_DF = 48000.0 / 4096.0            # sender FFT bin width, ~11.72 Hz
SPEC_NLIN = 23                         # linear bins before the log segment
_SPEC_FC = SPEC_FMIN + SPEC_NLIN * _SPEC_DF               # 299.53125 Hz
_SPEC_R = (SPEC_FMAX / _SPEC_FC) ** (1.0 / (SPEC_OUT - SPEC_NLIN))

# The 8 v3 band centers (geometric mean of BAND_EDGES_V3 in the sender),
# positioned on that axis — the smooth-curve control points.
_BAND_EDGES_HZ = np.array([20.0, 60.0, 120.0, 250.0, 500.0,
                           1000.0, 2500.0, 6000.0, 16000.0])
_BAND_CENTERS_HZ = np.sqrt(_BAND_EDGES_HZ[:-1] * _BAND_EDGES_HZ[1:])


def _axis_pos(f):
    """Frequency (Hz) -> continuous x position on the hybrid spectrum axis."""
    f = np.asarray(f, np.float64)
    lin = (f - SPEC_FMIN) / _SPEC_DF
    log = SPEC_NLIN + np.log(f / _SPEC_FC) / np.log(_SPEC_R)
    return np.where(f <= _SPEC_FC, lin, log)


def _curve_basis():
    """(SPEC_OUT x 8) matrix turning the 8 band values into a smooth curve
    sampled at every spectrum bin: per-frame cost is one tiny matmul.

    Cubic Hermite through the band centers with finite-difference
    (Catmull-Rom-style) tangents — LINEAR in the control values, so the
    whole spline collapses into a fixed matrix built by evaluating each
    unit vector once.  (PCHIP can't be a matrix: its slope limiting is
    data-dependent.)  Flat extension beyond the end bands; slight
    overshoot between contrasting bands is possible — consumers clamp >= 0.
    """
    cx = _axis_pos(_BAND_CENTERS_HZ)
    xs = np.arange(SPEC_OUT, dtype=np.float64)

    def eval_unit(y):
        m = np.empty(len(y))
        m[0] = (y[1] - y[0]) / (cx[1] - cx[0])
        m[1:-1] = (y[2:] - y[:-2]) / (cx[2:] - cx[:-2])
        m[-1] = (y[-1] - y[-2]) / (cx[-1] - cx[-2])
        out = np.empty(len(xs))
        for j, x in enumerate(xs):
            if x <= cx[0]:
                out[j] = y[0]
            elif x >= cx[-1]:
                out[j] = y[-1]
            else:
                i = int(np.searchsorted(cx, x)) - 1
                h = cx[i + 1] - cx[i]
                s = (x - cx[i]) / h
                out[j] = ((2 * s**3 - 3 * s**2 + 1) * y[i]
                          + (s**3 - 2 * s**2 + s) * h * m[i]
                          + (-2 * s**3 + 3 * s**2) * y[i + 1]
                          + (s**3 - s**2) * h * m[i + 1])
        return out

    return np.column_stack([eval_unit(np.eye(8)[k])
                            for k in range(8)]).astype(np.float32)


_CURVE_BASIS = _curve_basis()


class SpectrumChannel(Channel):
    """The feed's real spectrum + smooth band curves as a 128x1 RGBA32F texture.

    Unlike the 'audio' texture's spectrum row — a clamped 8-bit rFFT of the
    waveform, faithful to shadertoy.com and heavily compressed (everything
    >-30dB pins to 1.0) — .x is the sender's dedicated 4096-pt FFT: 128 bins
    from 30Hz..16kHz, dB-normalized to 0..1 with a generous music range, full
    float dynamic range, no waveform round-trip.  The x axis is a HYBRID
    linear+log scale: the first SPEC_NLIN (23) bins are linear at one FFT bin
    each (to ~300Hz), the rest log to 16kHz — so every bin carries real FFT
    data rather than interpolated holes in the low end.  Remap to all-log in
    the shader if you want that look; the wire stays max-entropy.

    .y and .z are SMOOTH CURVES through the 8 v3 band values on the same
    axis (Catmull-Rom-style, computed here from the milk bands — see
    _curve_basis): .y follows band imm (punchy), .z follows band env1 (the
    punchy flywheel — flowing).  A ready-made "polynomial fit of the bands"
    for silky spectrum-shaped visuals without per-bin noise; the LINEAR
    texture filter interpolates whatever you don't sample.  .w unused.

    Sample it like any 1-D LUT (linear filtered, so a normalized x works):
        float m = texture(iChannelN, vec2(x, 0.5)).x;   // x: 0=30Hz, 1=16kHz
        float c = texture(iChannelN, vec2(x, 0.5)).y;   // smooth band curve
    or fetch an exact bin with texelFetch(iChannelN, ivec2(i, 0), 0).

    With a v2 sender the 512-bin wire spectrum is mean-decimated 4:1 into .x
    (its axis constants differ slightly — transition-only); with a v0/v1
    sender everything is zero.  Gate on the milk texture's live/source flags
    if you need to know.
    """

    feed_driven = True
    WIDTH = SPEC_OUT

    def __init__(self):
        tex = make_texture(self.WIDTH, 1, bytes(self.WIDTH * 1 * 16),
                           filt=GL_LINEAR, wrap=GL_CLAMP_TO_EDGE,
                           internal=GL_RGBA32F, data_type=GL_FLOAT)
        super().__init__("spectrum", tex, self.WIDTH, 1)
        egl.check_gl("spectrum channel")
        self._buf = np.zeros((1, self.WIDTH, 4), np.float32)

    def update(self, features):
        spec = np.asarray(features.spec, np.float32)
        n = len(spec)
        if n != self.WIDTH:
            if n % self.WIDTH == 0:              # v2's 512 -> 4:1 mean decimate
                spec = spec.reshape(self.WIDTH, -1).mean(axis=1)
            else:
                spec = np.interp(np.linspace(0.0, n - 1, self.WIDTH),
                                 np.arange(n), spec)
        self._buf[0, :, 0] = spec                          # magnitude in .x
        self._buf[0, :, 1] = np.maximum(_CURVE_BASIS @ features.bands, 0.0)
        self._buf[0, :, 2] = np.maximum(
            _CURVE_BASIS @ features.band_env[:, 1], 0.0)
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
