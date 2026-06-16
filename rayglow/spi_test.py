#!/usr/bin/env python3
"""Static SPI test pattern — isolates the link from the GL renderer.

Sends a fixed, unambiguous frame through hub75.pack -> SpiOut, with NO OpenGL,
no shader, no readback. If the wall shows this pattern cleanly and right-side-up,
the packer + SPI + firmware are correct and any garbage in the live renderer is a
GL/readback problem. If the wall shows it wrong, the fault is in the link/firmware.

The pattern is authored in the FINAL display convention (row 0 = visual TOP,
col 0 = visual LEFT) and packed RAW (no flip), so what you see on the wall is the
ground-truth mapping from frame[y][x] to physical pixels.

What to look for:
  - Corner squares (8x8): TOP-LEFT = WHITE, top-right = RED,
                          bottom-left = GREEN, bottom-right = BLUE.
    -> tells you orientation + any mirror/flip at a glance.
  - Vertical blue gradient: DARK at top, BRIGHT at bottom.
    -> up/down sanity, independent of the corners.
  - Magenta horizontal line at row 32: the chain A | chain B seam.
  - Yellow vertical lines at x=64,128,192: the four-panel seams.
  - A 1px green border around the whole 256x64.

Run (on the rpi5):
    sudo ~/venv/bin/python -m rayglow.spi_test            # 8 MHz, safe
    sudo ~/venv/bin/python -m rayglow.spi_test --spi-hz 50000000
"""
from __future__ import annotations

import argparse
import sys
import time

import numpy as np

from .feed import config
from .render import hub75
from .render.spi_out import SpiOut


def build_pattern() -> np.ndarray:
    """(64,256,3) uint8, top-left origin (row 0 = top, col 0 = left)."""
    H, W = config.SPI_HEIGHT, config.SPI_WIDTH   # 64, 256 (logical wall)
    f = np.zeros((H, W, 3), np.uint8)

    # Vertical blue gradient: dark top -> bright bottom.
    f[:, :, 2] = (np.arange(H) * 255 // (H - 1)).astype(np.uint8)[:, None]

    # 1px green border.
    f[0, :] = f[-1, :] = (0, 120, 0)
    f[:, 0] = f[:, -1] = (0, 120, 0)

    # Panel seams (yellow verticals) + chain seam (magenta horizontal).
    for x in (64, 128, 192):
        f[:, x] = (120, 120, 0)
    f[32, :] = (160, 0, 160)

    # Corner markers (8x8) — orientation key.
    f[0:8, 0:8] = (255, 255, 255)     # TL white
    f[0:8, W - 8:W] = (255, 0, 0)     # TR red
    f[H - 8:H, 0:8] = (0, 255, 0)     # BL green
    f[H - 8:H, W - 8:W] = (0, 0, 255)  # BR blue
    return f


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--spi-hz", type=int, default=8_000_000,
                    help="SPI clock (default 8 MHz — low, to rule out bit-slip)")
    ap.add_argument("--ready-gpio", type=int, default=25)
    ap.add_argument("--flipv", action="store_true",
                    help="flip vertically before packing (top<->bottom)")
    ap.add_argument("--fliph", action="store_true",
                    help="flip horizontally before packing (left<->right)")
    args = ap.parse_args()

    frame = build_pattern()
    if args.flipv:
        frame = frame[::-1]
    if args.fliph:
        frame = frame[:, ::-1]
    frame = np.ascontiguousarray(frame)
    # Single-chain rig: fold the logical wall into the 512-wide serpentine strip.
    # This is the pattern that confirms SPI_CHAIN_ORDER / SPI_ROW_ROTATE_180.
    if config.SPI_SINGLE_CHAIN:
        frame = hub75.to_single_chain(frame)
    payload = hub75.pack(frame)
    print(f"spi_test: {frame.shape} -> {len(payload)} bytes, "
          f"flipv={args.flipv} fliph={args.fliph} single_chain={config.SPI_SINGLE_CHAIN}")

    out = SpiOut(args.spi_hz, ready_bcm=args.ready_gpio)
    n = 0
    try:
        while True:
            out.send(payload)
            n += 1
            if n % 60 == 0:
                print(f"sent {n} frames")
            time.sleep(1 / 120)   # don't busy-spam; the image is static
    except KeyboardInterrupt:
        pass
    finally:
        out.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
