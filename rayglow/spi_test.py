#!/usr/bin/env python3
"""Static test pattern over the link — isolates the link + cabling from the GL
renderer.

Sends a fixed, unambiguous frame through hub75.to_chains -> pack -> the link,
with NO OpenGL, no shader, no readback. If the wall shows this pattern cleanly
and right-side-up, the packer + fold + link + firmware are correct and any
garbage in the live renderer is a GL/readback problem. If the wall shows it
wrong, the fault is in the link/firmware/cabling — and the per-panel IDs below
tell you WHICH.

The pattern is authored in the FINAL display convention (row 0 = visual TOP,
col 0 = visual LEFT) and packed RAW (no flip), so what you see on the wall is the
ground-truth mapping from frame[y][x] to physical pixels.

What to look for:
  - Per-panel ID (the serpentine check, and the whole point on a folded wall):
    every panel carries CYAN dots along its top counting its COLUMN (1 dot =
    col 0 ... 6 dots = col 5) and ORANGE dots down its left counting its ROW.
    Read them across the wall: they must ascend left-to-right and top-to-bottom,
    all upright.
      * IDs upright but out of order  -> config.CHAIN_ORDER (cabling) is wrong.
      * A whole panel row upside down -> config.ROW_ROTATE_180 is wrong for it.
      * A row's IDs mirrored L<->R    -> that row's serpentine direction is
                                         reversed: flip `first_row_reversed`.
    Corner markers alone CANNOT catch these on a 24-panel wall — they only tag
    the four wall corners.
  - Corner squares (8x8): TOP-LEFT = WHITE, top-right = RED,
                          bottom-left = GREEN, bottom-right = BLUE.
    -> whole-wall orientation + any mirror/flip at a glance.
  - Vertical blue gradient: DARK at top, BRIGHT at bottom.
    -> up/down sanity, independent of the corners.
  - Magenta horizontal lines at each panel-row seam; yellow verticals at each
    panel-column seam; a 1px green border around the whole wall.

Both transports carry an identical byte stream, so pick the one you have flashed
(`--transport pio` needs phase6-parallel, `spi` needs phase5-spi). Confirming the
fold over the PRODUCTION pio link means no reflash just to check cabling.

Run (on the rpi5):
    sudo ~/venv/bin/python -m rayglow.spi_test                      # pio (default)
    sudo ~/venv/bin/python -m rayglow.spi_test --transport spi      # 8 MHz, safe
    sudo ~/venv/bin/python -m rayglow.spi_test --transport spi --spi-hz 50000000
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
    """(WALL_HEIGHT, WALL_WIDTH, 3) uint8, top-left origin. Geometry-robust: seams
    are drawn at panel boundaries so it works for any wall size (e.g. the
    single-chain 256x32 one-row A/B as well as the full 256x64)."""
    H, W = config.WALL_HEIGHT, config.WALL_WIDTH
    ph, pw = config.ROWS, config.COLS
    f = np.zeros((H, W, 3), np.uint8)

    # Vertical blue gradient: dark top -> bright bottom.
    f[:, :, 2] = (np.arange(H) * 255 // (H - 1)).astype(np.uint8)[:, None]

    # 1px green border.
    f[0, :] = f[-1, :] = (0, 120, 0)
    f[:, 0] = f[:, -1] = (0, 120, 0)

    # Panel seams: yellow verticals at each panel-column boundary, magenta
    # horizontals at each panel-row boundary (none for a single-row wall).
    for x in range(pw, W, pw):
        f[:, x] = (120, 120, 0)
    for y in range(ph, H, ph):
        f[y, :] = (160, 0, 160)

    # Per-panel ID — the serpentine/cabling key. Cyan dots along each panel's top
    # count its column (1-based), orange dots down its left count its row, so every
    # panel is uniquely and legibly tagged. This is what catches a mis-ordered or
    # mis-rotated panel on a folded wall, where the corner markers can't.
    # Dots are 3x3 on a 5px pitch, inset 10px from the panel's left edge so they
    # clear the 8x8 wall-corner markers below (which would otherwise erase the IDs
    # on the four corner panels) and the 1px border. Worst case still fits a 64x32
    # panel: col 5 -> x+10..x+37, row 3 -> y+10..y+27.
    for prow in range(H // ph):
        for pcol in range(W // pw):
            oy, ox = prow * ph, pcol * pw
            for i in range(pcol + 1):                       # column ID, cyan
                f[oy + 3:oy + 6, ox + 10 + i * 5:ox + 13 + i * 5] = (0, 255, 255)
            for i in range(prow + 1):                       # row ID, orange
                f[oy + 10 + i * 5:oy + 13 + i * 5, ox + 10:ox + 13] = (255, 140, 0)

    # Corner markers (8x8) — whole-wall orientation key.
    f[0:8, 0:8] = (255, 255, 255)      # TL white
    f[0:8, W - 8:W] = (255, 0, 0)      # TR red
    f[H - 8:H, 0:8] = (0, 255, 0)      # BL green
    f[H - 8:H, W - 8:W] = (0, 0, 255)  # BR blue
    return f


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--transport", choices=("pio", "spi"), default="pio",
                    help="link to send over: pio = the 4-lane parallel bus "
                         "(phase6-parallel, production), spi = the 1-lane "
                         "fallback (phase5-spi). Identical byte stream.")
    ap.add_argument("--spi-hz", type=int, default=8_000_000,
                    help="SPI clock (default 8 MHz — low, to rule out bit-slip)")
    ap.add_argument("--pio-clkdiv", type=int, default=3,
                    help="RP1 PIO clock divisor for --transport pio")
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
    # Fold the logical wall into the chains' electrical strips, then pack. This is
    # the pattern that confirms CHAIN_ORDER / ROW_ROTATE_180 (see the per-panel IDs
    # in the module docstring).
    if config.SINGLE_CHAIN:
        payload = hub75.pack_single(hub75.to_single_chain(frame))
    else:
        payload = hub75.pack(hub75.to_chains(frame))
    print(f"spi_test: wall {config.WALL_WIDTH}x{config.WALL_HEIGHT} "
          f"({config.PANEL_COLS}x{config.PANEL_ROWS} panels) -> "
          f"{config.PARALLEL_CHAINS} chain(s) of {config.CHAIN} "
          f"({config.CHAIN_WIDTH}px strip) -> {len(payload)} bytes over "
          f"{args.transport}")
    print(f"spi_test: firmware must have PANELS_IN_CHAIN = {config.CHAIN}; "
          f"flipv={args.flipv} fliph={args.fliph} single_chain={config.SINGLE_CHAIN}")

    if args.transport == "pio":
        from .render.pio_out import PioOut
        out = PioOut(clkdiv=args.pio_clkdiv, ready_bcm=args.ready_gpio)
    else:
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
