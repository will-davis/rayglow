#!/usr/bin/env python3
"""Domain coloring of a complex function for a 320x32 HUB75 LED matrix.

Each pixel maps to a complex number z = x + iy. We evaluate a complex polynomial
in product (root) form,

    f(z) = prod_k (z - root_k) ** winding_k

and color by the classic domain-coloring scheme:
    phase  arg(f)  -> Hue        (the swirling color wheels / vortices)
    |f|            -> Brightness  (smooth concentric rings = "symmetry pools")

Using the product form means phase and log-magnitude are just sums over the
roots (arg of a product is the sum of args; log|.| of a product is the sum of
logs) — numerically clean, no giant powers to overflow, and it lets us scatter
many roots across the very wide panel.

The roots are arranged as a repeating row of small rotating clusters. Each
cluster's roots sit on a circle — i.e. each cluster is a local z^n - 1 rosette
(the n-th roots of unity). Neighboring clusters have opposite winding so the
far field stays calm. The clusters rotate, breathe (radius oscillates), and
slowly scroll sideways, giving the "infinite, morphing" kaleidoscope.

Everything is numpy-vectorized over the full grid and blitted in one shot via
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
options.chain_length = 5
options.parallel = 1
options.disable_hardware_pulsing = 0       # snd_bcm2835 is blacklisted
options.gpio_slowdown = 5                   # tuned: 5 + lsb 130 ~eliminates panel-1 end-of-line artifact
options.brightness = 100
options.pwm_bits = 10
options.hardware_mapping = "adafruit-hat-pwm"  # GPIO4->GPIO18 jumper installed (hardware OE pulsing)
# options.pixel_mapper_config = "Rotate:180"  # disabled: panel physically flipped when 5th panel removed

WIDTH = options.cols * options.chain_length   # 320
HEIGHT = options.rows                         # 32
print(WIDTH, "x", HEIGHT)


# ---------------------------------------------------------------------------
# Domain-coloring parameters
# ---------------------------------------------------------------------------
RANGE_Y = 3.0          # complex-plane half-height the display spans (+/- this in imag)
N_ROOTS = 3            # roots per cluster -> the "n" of each local z^n - 1 rosette
CLUSTER_SPACING = 20.0 # horizontal gap (plane units) between cluster centers
ROOT_RADIUS = 6.4      # base circle radius of each cluster's roots
RADIUS_BREATHE = 2.95  # how much the cluster radius oscillates

ROT_SPEED = 0.55       # cluster rotation speed (radians/sec)
SCROLL_SPEED = 1.0     # sideways scroll of the whole pattern (plane units/sec)
HUE_DRIFT = 0.20       # slow global hue rotation (cycles/sec) for shimmer

CONTOUR = 0.9          # density of the magnitude brightness rings
V_LOW = 0.06           # darkest the ring valleys go (small -> deep black between pools)
EPS = 1e-6


# Pixel -> complex plane. SCALE maps the panel height to [-RANGE_Y, +RANGE_Y];
# the width then spans a much wider real range (the panel is 10:1).
SCALE = HEIGHT / (2.0 * RANGE_Y)
YS, XS = np.indices((HEIGHT, WIDTH))
PX = (XS.astype(np.float32) - WIDTH / 2.0) / SCALE     # real part, shape (H, W)
PY = (YS.astype(np.float32) - HEIGHT / 2.0) / SCALE    # imag part, shape (H, W)

PLANE_HALF_W = (WIDTH / 2.0) / SCALE                   # real-axis half-extent on screen


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


def root_positions(now):
    """Return (px, py, winding) lists for all roots at time `now`."""
    theta = now * ROT_SPEED
    radius = ROOT_RADIUS + RADIUS_BREATHE * math.sin(now * 0.5)
    scroll = (now * SCROLL_SPEED) % CLUSTER_SPACING

    xs_pts, ys_pts, wnd = [], [], []
    # Cover the visible real range plus a cluster of margin on each side so
    # roots scroll smoothly in/out of frame.
    k = -1
    x_start = -PLANE_HALF_W - CLUSTER_SPACING + scroll
    while True:
        cx = x_start + k * CLUSTER_SPACING
        if cx > PLANE_HALF_W + CLUSTER_SPACING:
            break
        sign = 1.0 if (k % 2 == 0) else -1.0       # alternate winding between clusters
        for j in range(N_ROOTS):
            ang = theta + 2.0 * math.pi * j / N_ROOTS
            xs_pts.append(cx + radius * math.cos(ang))
            ys_pts.append(radius * math.sin(ang))
            wnd.append(sign)
        k += 1
    return xs_pts, ys_pts, wnd


matrix = RGBMatrix(options=options)
canvas = matrix.CreateFrameCanvas()
print("Matrix initialized\n")

start = time()
while True:
    now = time() - start

    # Accumulate phase (sum of args) and log-magnitude (sum of logs) over roots.
    phase = np.zeros((HEIGHT, WIDTH), dtype=np.float32)
    logmag = np.zeros((HEIGHT, WIDTH), dtype=np.float32)
    xs_pts, ys_pts, wnd = root_positions(now)
    for px, py, w in zip(xs_pts, ys_pts, wnd):
        dx = PX - px
        dy = PY - py
        phase += w * np.arctan2(dy, dx)
        logmag += w * 0.5 * np.log(dx * dx + dy * dy + EPS)

    # Phase -> hue (with a slow global drift); magnitude -> smooth ring brightness.
    hue = phase / (2.0 * math.pi) + now * HUE_DRIFT
    shade = 0.5 - 0.5 * np.cos(2.0 * math.pi * logmag * CONTOUR)
    value = V_LOW + (1.0 - V_LOW) * shade
    sat = np.ones_like(value)

    r, g, b = hsv_to_rgb(hue, sat, value)
    frame = (np.clip(np.stack([r, g, b], axis=-1), 0.0, 1.0) * 255.0).astype(np.uint8)
    image = Image.fromarray(frame, "RGB")

    canvas.SetImage(image)
    canvas = matrix.SwapOnVSync(canvas)

    sleep(0.005)
