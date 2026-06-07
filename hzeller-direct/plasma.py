#!/usr/bin/env python3
"""Plasma visualization for a 320x32 HUB75 LED matrix.

Sums several interfering sine/cosine waves at multiple angles into a scalar
field, then maps that field through a phase-shifting RGB sine palette to get
shifting, iridescent gradients — the oil-slick / liquid-crystal look.

The field is the classic plasma form,

    P(x,y,t) = sin(x/a + t)
             + cos(y/b + t)
             + sin(sqrt(x^2 + y^2)/c + t)

plus an angled wave and a moving-center radial term for extra "liquid" motion.

Everything is computed with numpy over the whole 320x32 grid at once and blitted
as a single PIL image via canvas.SetImage() — per-pixel Python SetPixel calls
would be far too slow for 10k pixels/frame.
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
# Plasma tunables
# ---------------------------------------------------------------------------
A = 16.0          # x sine wavelength
B = 8.0           # y cosine wavelength
C = 14.0          # radial (from origin) wavelength
D = 20.0          # angled-wave wavelength
ANGLE = 0.6       # direction (radians) of the angled wave
E = 10.0          # moving-center radial wavelength

TIME_SCALE = 1.4      # overall animation speed
COLOR_SCALE = 0.35    # how fast the palette hue drifts (oil-slick shimmer)
NUM_TERMS = 5.0       # field is normalized by this to land roughly in [-1, 1]

# Phase offsets for the three RGB channels (120 degrees apart -> full spectrum).
PHASE_R = 0.0
PHASE_G = 2.0 * math.pi / 3.0
PHASE_B = 4.0 * math.pi / 3.0

# Luminance (brightness) field — a SECOND, independent interference pattern that
# scales each pixel's brightness so light pools and drains across the colors.
# Kept on its own frequencies/phase so the bright bands sweep through the hues
# rather than being locked to them.
LUM_A = 26.0          # angled-wave wavelength for the brightness field
LUM_B = 18.0          # moving-radial wavelength for the brightness field
LUM_SPEED = 0.8       # how fast the bright/dark pools travel
LUM_GAMMA = 1.8       # >1 deepens the dark valleys for more contrast/pop
LUM_FLOOR = 0.04      # minimum brightness so darks aren't dead-black


# Precompute the coordinate grids once — they never change frame to frame.
# xs, ys have shape (HEIGHT, WIDTH) so the final image array is row-major (y, x).
xs, ys = np.meshgrid(np.arange(WIDTH, dtype=np.float32),
                     np.arange(HEIGHT, dtype=np.float32))
radius_origin = np.sqrt(xs * xs + ys * ys)                 # distance from (0,0)
angled = xs * math.cos(ANGLE) + ys * math.sin(ANGLE)       # projection onto ANGLE


matrix = RGBMatrix(options=options)
canvas = matrix.CreateFrameCanvas()
print("Matrix initialized\n")

start = time()
while True:
    t = (time() - start) * TIME_SCALE

    # Moving center traces a slow ellipse across the wide display.
    cx = WIDTH * (0.5 + 0.45 * math.sin(t * 0.5))
    cy = HEIGHT * (0.5 + 0.45 * math.cos(t * 0.37))
    radius_moving = np.sqrt((xs - cx) ** 2 + (ys - cy) ** 2)

    # Sum the interfering waves into the plasma field.
    field = (np.sin(xs / A + t)
             + np.cos(ys / B + t)
             + np.sin(radius_origin / C + t)
             + np.sin(angled / D - t * 0.7)
             + np.sin(radius_moving / E + t * 1.3))

    # Normalize to ~[-1, 1] and feed a phase-shifting RGB sine palette.
    nv = field / NUM_TERMS
    phase = math.pi * nv + t * COLOR_SCALE
    r = 0.5 + 0.5 * np.sin(phase + PHASE_R)
    g = 0.5 + 0.5 * np.sin(phase + PHASE_G)
    b = 0.5 + 0.5 * np.sin(phase + PHASE_B)

    # Independent luminance field: a slower, larger-scale interference pattern
    # that travels through the colors. Gamma deepens the valleys; the floor
    # keeps a faint glow instead of dead black.
    lum = (np.sin(angled / LUM_A + t * 0.9)
           + np.sin(radius_moving / LUM_B - t * 0.6))
    bright = 0.5 + 0.5 * np.sin(math.pi * (lum / 2.0) + t * LUM_SPEED)
    bright = LUM_FLOOR + (1.0 - LUM_FLOOR) * bright ** LUM_GAMMA

    # Stack into an (H, W, 3) image, scale by the brightness field, blit at once.
    rgb = np.stack([r, g, b], axis=-1) * bright[:, :, np.newaxis]
    frame = (rgb * 255.0).astype(np.uint8)
    image = Image.fromarray(frame, "RGB")

    canvas.SetImage(image)
    canvas = matrix.SwapOnVSync(canvas)

    sleep(0.01)
