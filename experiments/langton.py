#!/usr/bin/env python3
"""Multi-species Langton's Ant for a 256x32 HUB75 LED matrix.

Several "ants" of different colors walk a shared toroidal grid, each obeying
the classic Langton rule against the cell it stands on:

    black cell   -> turn 90 deg one way,  paint the cell the ant's color
    colored cell -> turn 90 deg the other way, revert the cell to black

(any color counts as "colored" — so ants erase and disrupt each other's
trails, which is what fractures highways when they collide).

Each ant has a chirality (clockwise-first or counterclockwise-first); both
kinds build highways, mirrored. The expected life cycle on the panel:

    1. seconds of chaotic, symmetric-ish pixel noise around each ant
    2. sudden order: diagonal "highways" extruding across the matrix
    3. highways hit other ants' debris or wrap around the torus -> fracture,
       re-chaos, sometimes a new highway in a new direction

A decaying "glow" layer adds a white spark wherever a cell was just flipped,
so the active tip of each ant reads as a bright comet head. When the grid
gets too cluttered for highways to survive (coverage threshold) or an epoch
runs too long, the display fades out and reseeds fresh ants.

Cell state is a (H, W) uint8 grid (0 = black, n = species n); rendering is a
single palette lookup + glow add, blitted with canvas.SetImage(). Only the
ant stepping itself is a Python loop — a few hundred scalar steps per frame.
"""

import colorsys
import math
import random
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
options.pwm_bits = 10
options.hardware_mapping = "adafruit-hat-pwm"  # GPIO4->GPIO18 jumper installed (hardware OE pulsing)

WIDTH = options.cols * options.chain_length   # 256
HEIGHT = options.rows                         # 32
print(WIDTH, "x", HEIGHT)


# ---------------------------------------------------------------------------
# Langton tunables
# ---------------------------------------------------------------------------
NUM_ANTS = 6            # number of ants / species (each gets its own hue)
STEPS_PER_FRAME = 10    # ant-steps per ant per frame; higher = faster evolution.
                        # 10 @ 60fps -> ~17 s of chaos before the first highways.
MIRROR_EVERY_OTHER = True  # alternate chirality ant-to-ant (mirrored highways);
                           # False = all ants same handedness

RESET_COVERAGE = 0.45   # reseed when this fraction of cells is colored.
                        # Equilibrium hovers just under ~0.55, so 0.45 fires at
                        # "max clutter"; sweep-tested -> ~37 s epochs. >=0.55
                        # would never reset (rely on MAX_EPOCH_SEC instead).
MAX_EPOCH_SEC = 680     # reseed after this long even if coverage stays low
FADE_FRAMES = 40        # frames for the fade-to-black between epochs

GLOW_DECAY = 0.88       # per-frame decay of the just-flipped spark layer
GLOW_WHITE = 110.0      # how much white a fresh flip adds (0..255-ish)
HEAD_COLOR = (255, 255, 255)  # the ant itself, drawn on top each frame

SATURATION = 1.0        # species palette: HSV saturation
VALUE = 0.95            # species palette: HSV value (trail brightness)
HUE_OFFSET = 0.0        # rotate the whole palette (0..1)

TARGET_FPS = 60


# ---------------------------------------------------------------------------
# Species palette: NUM_ANTS evenly spaced hues; index 0 stays black.
# ---------------------------------------------------------------------------
palette = np.zeros((NUM_ANTS + 1, 3), dtype=np.float32)
for i in range(NUM_ANTS):
    h = (HUE_OFFSET + i / NUM_ANTS) % 1.0
    palette[i + 1] = [c * 255.0 for c in colorsys.hsv_to_rgb(h, SATURATION, VALUE)]

# Direction encoding: 0=N, 1=E, 2=S, 3=W  (y grows downward).
DX = (0, 1, 0, -1)
DY = (-1, 0, 1, 0)


class Ant:
    __slots__ = ("x", "y", "d", "species", "chir")

    def __init__(self, x, y, d, species, chir):
        self.x, self.y, self.d = x, y, d
        self.species, self.chir = species, chir


def reseed(state, glow):
    """Clear the grid and spawn ants spread along the width, random rows."""
    state[:] = 0
    glow[:] = 0.0
    ants = []
    for i in range(NUM_ANTS):
        x = int((i + 0.5) * WIDTH / NUM_ANTS + random.uniform(-6, 6)) % WIDTH
        y = random.randint(HEIGHT // 4, 3 * HEIGHT // 4)
        chir = -1 if (MIRROR_EVERY_OTHER and i % 2) else 1
        ants.append(Ant(x, y, random.randrange(4), i + 1, chir))
    return ants


state = np.zeros((HEIGHT, WIDTH), dtype=np.uint8)   # 0 = black, n = species n
glow = np.zeros((HEIGHT, WIDTH), dtype=np.float32)  # just-flipped spark layer
ants = reseed(state, glow)

matrix = RGBMatrix(options=options)
canvas = matrix.CreateFrameCanvas()
print("Matrix initialized\n")


def render():
    """Palette-lookup the grid, add the glow spark layer, return uint8 frame."""
    rgb = palette[state]                              # (H, W, 3) float32
    rgb += glow[:, :, np.newaxis] * (GLOW_WHITE / 255.0) * 255.0
    return rgb


frame_dt = 1.0 / TARGET_FPS
epoch_start = time()

while True:
    t0 = time()

    # --- step every ant a batch of moves -----------------------------------
    for ant in ants:
        x, y, d, chir, species = ant.x, ant.y, ant.d, ant.chir, ant.species
        for _ in range(STEPS_PER_FRAME):
            if state[y, x]:
                d = (d - chir) % 4      # colored: turn the other way, erase
                state[y, x] = 0
            else:
                d = (d + chir) % 4      # black: turn, paint our color
                state[y, x] = species
            glow[y, x] = 1.0
            x = (x + DX[d]) % WIDTH
            y = (y + DY[d]) % HEIGHT
        ant.x, ant.y, ant.d = x, y, d

    # --- render -------------------------------------------------------------
    rgb = render()
    frame = np.clip(rgb, 0, 255).astype(np.uint8)
    # Ant heads on top, full white.
    for ant in ants:
        frame[ant.y, ant.x] = HEAD_COLOR
    canvas.SetImage(Image.fromarray(frame, "RGB"))
    canvas = matrix.SwapOnVSync(canvas)

    glow *= GLOW_DECAY

    # --- epoch management: fade out + reseed when saturated or stale --------
    coverage = np.count_nonzero(state) / state.size
    if coverage > RESET_COVERAGE or (time() - epoch_start) > MAX_EPOCH_SEC:
        for f in np.linspace(1.0, 0.0, FADE_FRAMES):
            frame = np.clip(render() * f, 0, 255).astype(np.uint8)
            canvas.SetImage(Image.fromarray(frame, "RGB"))
            canvas = matrix.SwapOnVSync(canvas)
            sleep(frame_dt)
        ants = reseed(state, glow)
        epoch_start = time()

    # --- pace ---------------------------------------------------------------
    elapsed = time() - t0
    if elapsed < frame_dt:
        sleep(frame_dt - elapsed)
