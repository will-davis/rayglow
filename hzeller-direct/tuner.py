#!/usr/bin/env python3
"""Artifact-tuning harness for the end-of-line glitch hunt.

Displays a worst-case stress pattern and takes the timing knobs as command-line
arguments, so different (slowdown, lsb, pwm-bits) combinations can be tried in
seconds without editing any files:

    sudo ~/rgbvenv/bin/python tuner.py --slowdown 5 --lsb 130
    sudo ~/rgbvenv/bin/python tuner.py --slowdown 4 --lsb 100 --pwm-bits 8
    sudo ~/rgbvenv/bin/python tuner.py --slowdown 4 --lsb 130 --show-refresh

The pattern is built to make the artifact maximally visible:
  * full-width horizontal gray gradient  -> exercises every bit-plane combination
    along the line, so any plane's misbehavior shows somewhere
  * last 24 columns split black (top half) / white (bottom half) -> spurious
    lit pixels pop against the black; dropouts pop against the white. This is
    the end-of-line region where the corruption lives.
  * thin full-saturation RGB rows at the artifact rows (1-4, 16-20) so color
    corruption there is obvious
  * optional --animate sweeps a white column so dynamic behavior is visible too

--show-refresh passes the library's own refresh-rate display through (prints
continuously to the terminal) so each combo's Hz can be recorded.
"""

import argparse
from time import sleep

import numpy as np
from PIL import Image

from rgbmatrix import RGBMatrix, RGBMatrixOptions


parser = argparse.ArgumentParser(description="LED matrix timing-artifact tuner")
parser.add_argument("--slowdown", type=int, default=4, help="gpio_slowdown (default 4)")
parser.add_argument("--lsb", type=int, default=130,
                    help="pwm_lsb_nanoseconds (default 130)")
parser.add_argument("--pwm-bits", type=int, default=10, help="pwm_bits (default 10)")
parser.add_argument("--dither", type=int, default=0, help="pwm_dither_bits (default 0)")
parser.add_argument("--brightness", type=int, default=100, help="brightness (default 100)")
parser.add_argument("--show-refresh", action="store_true",
                    help="print the actual refresh rate continuously")
parser.add_argument("--animate", action="store_true",
                    help="sweep a white column across the panel")
args = parser.parse_args()

options = RGBMatrixOptions()
options.rows = 32
options.cols = 64
options.chain_length = 4
options.parallel = 1
options.disable_hardware_pulsing = 0       # hardware OE pulsing (GPIO4->18 jumper)
options.gpio_slowdown = args.slowdown
options.brightness = args.brightness
options.pwm_bits = args.pwm_bits
options.pwm_lsb_nanoseconds = args.lsb
options.pwm_dither_bits = args.dither
options.show_refresh_rate = 1 if args.show_refresh else 0
options.hardware_mapping = "adafruit-hat-pwm"

WIDTH = options.cols * options.chain_length
HEIGHT = options.rows

print("tuner: slowdown=%d lsb=%dns pwm_bits=%d dither=%d brightness=%d"
      % (args.slowdown, args.lsb, args.pwm_bits, args.dither, args.brightness))

# ---------------------------------------------------------------------------
# Build the static stress frame
# ---------------------------------------------------------------------------
frame = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)

# Horizontal gray gradient (8..255) across the full width: every bit pattern.
grad = np.linspace(8, 255, WIDTH).astype(np.uint8)
frame[:, :, :] = grad[None, :, None]

# Saturated color rows at the artifact-prone scan rows (and their mirrors).
for r, color in ((1, (255, 0, 0)), (2, (0, 255, 0)), (3, (0, 0, 255)),
                 (4, (255, 255, 0))):
    frame[r, :] = color
    frame[r + 16, :] = color

# End-of-line probe block: last 24 columns black on top half, white on bottom.
frame[:16, WIDTH - 24:] = 0
frame[16:, WIDTH - 24:] = 255

matrix = RGBMatrix(options=options)
canvas = matrix.CreateFrameCanvas()
print("Matrix initialized — watch the last ~24 columns of panel 1. Ctrl-C quits.")

phase = 0
try:
    while True:
        if args.animate:
            f = frame.copy()
            f[:, phase % WIDTH] = 255
            phase += 1
        else:
            f = frame
        canvas.SetImage(Image.fromarray(f, "RGB"))
        canvas = matrix.SwapOnVSync(canvas)
        sleep(0.03)
except KeyboardInterrupt:
    pass
