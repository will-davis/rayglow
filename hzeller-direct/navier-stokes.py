#!/usr/bin/env python3
"""Real-time fluid dynamics for a 320x32 HUB75 LED matrix (Lattice Boltzmann).

A D2Q9 Lattice Boltzmann solver evolves an *invisible* velocity field across the
whole panel: 9 discrete velocity populations per cell, a BGK collision step, and a
streaming step (a lattice shift, done with np.roll). The domain is periodic, kept
moving by a few localized jets (a body force) and stirred by a small solid
obstacle that deflects the flow and sheds eddies.

Three colored *dye* fields (R, G, B) are injected continuously at the jets and
advected through the velocity field by semi-Lagrangian backtrace. The dye is the
only thing you see — it reveals the otherwise invisible flow as it shears, folds,
and mixes.

Everything is numpy-vectorized over the full grid; the finished dye image is
blitted in one shot via canvas.SetImage() (the fast Cython path).

D2Q9 lattice:
    indices  0   1   2   3   4    5    6    7    8
    e_x      0   1   0  -1   0    1   -1   -1    1
    e_y      0   0   1   0  -1    1    1   -1   -1
    opposite 0   3   4   1   2    7    8    5    6   (for no-slip bounce-back)
"""

import math
from time import sleep

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
# LBM parameters
# ---------------------------------------------------------------------------
TAU = 0.51                 # relaxation time; viscosity nu = (TAU-0.5)/3. Must be > 0.5.
OMEGA = 1.0 / TAU          # collision frequency
FORCE = 0.008              # jet body-force magnitude (keep small -> |u| stays << 1)

DYE_ADV = 4.0              # how many velocity-steps the dye advects per frame (visual speed)
DYE_FADE = 0.992           # per-frame dye decay so old streaks fade out
GAMMA = 0.75               # output gamma (<1 lifts the dim dye so faint wisps show)

# D2Q9 lattice constants
EX = np.array([0, 1, 0, -1, 0, 1, -1, -1, 1], dtype=np.float32)
EY = np.array([0, 0, 1, 0, -1, 1, 1, -1, -1], dtype=np.float32)
W = np.array([4/9, 1/9, 1/9, 1/9, 1/9, 1/36, 1/36, 1/36, 1/36], dtype=np.float32)
OPP = np.array([0, 3, 4, 1, 2, 7, 8, 5, 6])
NQ = 9


# ---------------------------------------------------------------------------
# Field / geometry setup
# ---------------------------------------------------------------------------
# Coordinate grids (XS = column/x index, YS = row/y index), shape (HEIGHT, WIDTH).
YS, XS = np.indices((HEIGHT, WIDTH))
XS = XS.astype(np.float32)
YS = YS.astype(np.float32)


def disc(cx, cy, r):
    """Boolean mask of a filled disc centered at (cx, cy) with radius r."""
    return (XS - cx) ** 2 + (YS - cy) ** 2 <= r * r


# Solid obstacle (no-slip bounce-back) — a small post the flow has to wrap around.
solid = disc(190, 15, 3.5)

# Jets: each continuously pushes fluid (body force) and injects one dye color.
# Vertical components are opposed so the colored streams shear past each other.
JETS = [
    # (mask,                 force_x, force_y, dye_color (r,g,b))
    (disc(18, 16, 3),        1.0,  0.15, (1.0, 0.05, 0.05)),   # red,   pushing right + slightly down
    (disc(70, 6, 3),         0.9,  0.55, (0.05, 1.0, 0.10)),   # green, pushing right + down
    (disc(70, 26, 3),        0.9, -0.55, (0.10, 0.15, 1.0)),   # blue,  pushing right + up
]

# Build the static body-force field (acceleration per cell) from the jets.
ACC_X = np.zeros((HEIGHT, WIDTH), dtype=np.float32)
ACC_Y = np.zeros((HEIGHT, WIDTH), dtype=np.float32)
for mask, fx, fy, _color in JETS:
    ACC_X[mask] += FORCE * fx
    ACC_Y[mask] += FORCE * fy
ACC_X[solid] = 0.0
ACC_Y[solid] = 0.0


def equilibrium(rho, ux, uy):
    """D2Q9 BGK equilibrium distribution feq, shape (9, HEIGHT, WIDTH)."""
    cu = 3.0 * (EX[:, None, None] * ux[None] + EY[:, None, None] * uy[None])
    usq = 1.5 * (ux * ux + uy * uy)
    return W[:, None, None] * rho[None] * (1.0 + cu + 0.5 * cu * cu - usq[None])


# Initialize populations to rest equilibrium (rho = 1, u = 0) with a faint
# perturbation to break perfect symmetry so instabilities can grow.
f = np.ones((NQ, HEIGHT, WIDTH), dtype=np.float32) * W[:, None, None]
f *= (1.0 + 0.01 * np.random.standard_normal((NQ, HEIGHT, WIDTH)).astype(np.float32))

# Dye field (RGB), shape (HEIGHT, WIDTH, 3).
dye = np.zeros((HEIGHT, WIDTH, 3), dtype=np.float32)


def lbm_step():
    """One LBM collide + bounce-back + stream step. Returns macroscopic (ux, uy)."""
    global f

    rho = f.sum(axis=0)
    ux = (EX[:, None, None] * f).sum(axis=0) / rho
    uy = (EY[:, None, None] * f).sum(axis=0) / rho

    # Body force via the shifted-velocity (Shan-Chen style) method.
    ux_eq = ux + TAU * ACC_X
    uy_eq = uy + TAU * ACC_Y

    feq = equilibrium(rho, ux_eq, uy_eq)
    fpost = f + OMEGA * (feq - f)

    # No-slip bounce-back: reflect pre-collision populations inside the obstacle.
    for i in range(NQ):
        fpost[i][solid] = f[OPP[i]][solid]

    # Streaming: shift each population along its lattice direction (periodic).
    for i in range(NQ):
        f[i] = np.roll(fpost[i], (int(EY[i]), int(EX[i])), axis=(0, 1))

    return ux, uy


def advect_dye(ux, uy):
    """Semi-Lagrangian backtrace of the RGB dye through the velocity field (periodic)."""
    global dye

    bx = XS - ux * DYE_ADV
    by = YS - uy * DYE_ADV

    x0 = np.floor(bx).astype(np.int32)
    y0 = np.floor(by).astype(np.int32)
    fx = (bx - x0)[:, :, None]
    fy = (by - y0)[:, :, None]
    x0 %= WIDTH
    y0 %= HEIGHT
    x1 = (x0 + 1) % WIDTH
    y1 = (y0 + 1) % HEIGHT

    dye = (dye[y0, x0] * (1 - fx) * (1 - fy)
           + dye[y0, x1] * fx * (1 - fy)
           + dye[y1, x0] * (1 - fx) * fy
           + dye[y1, x1] * fx * fy) * DYE_FADE

    # Re-inject dye at the jets; clear it inside the solid obstacle.
    for mask, _fx, _fy, color in JETS:
        dye[mask] = color
    dye[solid] = 0.0


matrix = RGBMatrix(options=options)
canvas = matrix.CreateFrameCanvas()
print("Matrix initialized\n")

while True:
    ux, uy = lbm_step()
    advect_dye(ux, uy)

    frame = (np.clip(dye, 0.0, 1.0) ** GAMMA * 255.0).astype(np.uint8)
    image = Image.fromarray(frame, "RGB")

    canvas.SetImage(image)
    canvas = matrix.SwapOnVSync(canvas)

    sleep(0.005)
