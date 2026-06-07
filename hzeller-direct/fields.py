#!/usr/bin/env python3
"""Dynamic vector-field particle system for a 320x32 HUB75 LED matrix.

Several hundred sub-pixel particles flow through an underlying vector field and
leave fading trails on a decay buffer, producing smooth, sweeping stellar-nebula
streams.

The field is sampled for velocity (not iterated as a map): each particle reads
v = F(x, y) and integrates its position. Three fields are available and cycle
over time, one parameter slowly morphing for a "gravitational warping" feel:

    flow      F = (sin y, cos x)                      -- the classic analytic field
    clifford  F = (sin(a y) + c cos(a x),             -- Clifford attractor formula
                   sin(b x) + d cos(b y))                used as a flow field
    dejong    F = (sin(a y) - cos(b x),               -- Peter de Jong formula
                   sin(c x) - cos(d y))                  used as a flow field

Sampling the attractor *formulas* as a continuous field (rather than iterating
the map, which would collapse every particle onto the attractor curve) keeps the
particles flowing forever; and since the formulas are all sin/cos they're
periodic, so the flow tiles naturally across the very wide panel.

Trails come from a DECAY buffer: each frame the buffer dims by DECAY and every
particle bilinearly splats color into it (additive). Dense regions sum past 1.0
and clip to white -> hot nebula cores. Particles are colored by flow direction.

Everything is numpy-vectorized; the buffer is blitted in one shot via
canvas.SetImage() (the fast Cython path).
"""

import math
from time import time, sleep

import numpy as np
from PIL import Image

from rgbmatrix import RGBMatrix, RGBMatrixOptions


# ---------------------------------------------------------------------------
# Matrix hardware config (matches the rest of this rig — see CLAUDE.md / memory)
# ---------------------------------------------------------------------------
options = RGBMatrixOptions()
options.rows = 32
options.cols = 64
options.chain_length = 4
options.parallel = 1
options.disable_hardware_pulsing = 0       # snd_bcm2835 is blacklisted
options.gpio_slowdown = 5                   # tuned: 5 + lsb 130 ~eliminates panel-1 end-of-line artifact
options.brightness = 100
options.pwm_lsb_nanoseconds = 130
options.pwm_bits = 10
options.hardware_mapping = "adafruit-hat-pwm"  # GPIO4->GPIO18 jumper installed (hardware OE pulsing)
# options.pixel_mapper_config = "Rotate:180"  # disabled: panel physically flipped when 5th panel removed

WIDTH = options.cols * options.chain_length   # 256
HEIGHT = options.rows                         # 32
print(WIDTH, "x", HEIGHT)


# ---------------------------------------------------------------------------
# Particle / field parameters
# ---------------------------------------------------------------------------
N_PARTICLES = 700

FIELD_SCALE = 7.0      # pixels per field-space unit (sets the swirl wavelength)
SPEED = 0.9            # particle step size multiplier (pixels per frame ~ SPEED*|F|)
DECAY = 0.97           # per-frame trail fade (higher = longer, smearier trails)
DEPOSIT = 0.09         # color added per particle per frame (additive into the buffer)
GAMMA = 0.70           # output gamma (<1 lifts faint trails)
SAT = 0.95             # color saturation (lower = more pastel / nebula-like)
HUE_DRIFT = 0.03       # slow global hue rotation (cycles/sec)

LIFE_MIN, LIFE_MAX = 150, 420    # particle lifetime range (frames) before respawn
CYCLE_SECONDS = 25.0             # how long each field stays active

INV_SCALE = 1.0 / FIELD_SCALE
CX = WIDTH / 2.0
CY = HEIGHT / 2.0


# Field definitions: name + base parameters. One param is time-morphed below.
FIELDS = [
    ("flow",     {}),
    ("clifford", {"a": 1.4, "b": 1.7, "c": 1.0, "d": 0.7}),
    ("dejong",   {"a": 1.4, "b": -2.3, "c": 1.6, "d": -2.1}),
]


def field_velocity(px, py, name, p, now):
    """Vector field sampled at pixel coords (px, py). Returns (vx, vy) in field units."""
    u = (px - CX) * INV_SCALE          # field-space coordinates
    v = (py - CY) * INV_SCALE
    warp = 0.15 * math.sin(now * 0.2)  # slow parameter morph -> gravitational warping

    if name == "flow":
        vx = np.sin(v) + 0.5 * np.cos(u * 0.4 + warp)
        vy = np.cos(u) + 0.5 * np.sin(v * 1.3)
    elif name == "clifford":
        a, b, c, d = p["a"] + warp, p["b"], p["c"], p["d"]
        vx = np.sin(a * v) + c * np.cos(a * u)
        vy = np.sin(b * u) + d * np.cos(b * v)
    else:  # dejong
        a, b, c, d = p["a"] + warp, p["b"], p["c"], p["d"]
        vx = np.sin(a * v) - np.cos(b * u)
        vy = np.sin(c * u) - np.cos(d * v)
    return vx, vy


def hsv_to_rgb(h, s, v):
    """Vectorized HSV->RGB for float arrays (h, s, v in [0,1]). Returns 3 arrays in [0,1]."""
    h6 = (h % 1.0) * 6.0
    i = np.floor(h6).astype(np.int32) % 6
    f = h6 - np.floor(h6)
    p = v * (1.0 - s)
    q = v * (1.0 - s * f)
    t = v * (1.0 - s * (1.0 - f))
    r = np.choose(i, [v, q, p, p, t, v])
    g = np.choose(i, [t, v, v, q, p, p])
    b = np.choose(i, [p, p, t, v, v, q])
    return r, g, b


# Particle state: float positions, per-particle remaining lifetime.
px = np.random.uniform(0, WIDTH, N_PARTICLES).astype(np.float32)
py = np.random.uniform(0, HEIGHT, N_PARTICLES).astype(np.float32)
life = np.random.randint(LIFE_MIN, LIFE_MAX, N_PARTICLES)

# Decay buffer (RGB), shape (HEIGHT, WIDTH, 3).
buf = np.zeros((HEIGHT, WIDTH, 3), dtype=np.float32)


def respawn(mask):
    """Re-seed the masked particles at random positions with fresh lifetimes."""
    n = int(mask.sum())
    if n == 0:
        return
    px[mask] = np.random.uniform(0, WIDTH, n)
    py[mask] = np.random.uniform(0, HEIGHT, n)
    life[mask] = np.random.randint(LIFE_MIN, LIFE_MAX, n)


matrix = RGBMatrix(options=options)
canvas = matrix.CreateFrameCanvas()
print("Matrix initialized\n")

start = time()
while True:
    now = time() - start
    name, params = FIELDS[int(now / CYCLE_SECONDS) % len(FIELDS)]

    # Sample the field for velocity, color particles by flow direction.
    vx, vy = field_velocity(px, py, name, params, now)
    hue = (np.arctan2(vy, vx) / (2.0 * math.pi) + now * HUE_DRIFT)
    cr, cg, cb = hsv_to_rgb(hue, SAT, 1.0)
    col = np.stack([cr, cg, cb], axis=-1) * DEPOSIT      # (N, 3)

    # Integrate motion.
    px += vx * SPEED
    py += vy * SPEED

    # Respawn particles that leave the panel or age out.
    life -= 1
    respawn((px < 0) | (px >= WIDTH) | (py < 0) | (py >= HEIGHT) | (life <= 0))

    # Fade the trail buffer, then bilinearly splat each particle into it.
    buf *= DECAY
    x0 = np.floor(px).astype(np.int32)
    y0 = np.floor(py).astype(np.int32)
    fx = px - x0
    fy = py - y0
    ok = (x0 >= 0) & (x0 < WIDTH - 1) & (y0 >= 0) & (y0 < HEIGHT - 1)
    xi, yi = x0[ok], y0[ok]
    fxi, fyi = fx[ok, None], fy[ok, None]
    ci = col[ok]
    np.add.at(buf, (yi, xi), (1 - fxi) * (1 - fyi) * ci)
    np.add.at(buf, (yi, xi + 1), fxi * (1 - fyi) * ci)
    np.add.at(buf, (yi + 1, xi), (1 - fxi) * fyi * ci)
    np.add.at(buf, (yi + 1, xi + 1), fxi * fyi * ci)

    frame = (np.clip(buf, 0.0, 1.0) ** GAMMA * 255.0).astype(np.uint8)
    image = Image.fromarray(frame, "RGB")

    canvas.SetImage(image)
    canvas = matrix.SwapOnVSync(canvas)

    sleep(0.005)
