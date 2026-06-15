"""Bit-plane packer: full-display RGB frame -> rp2350b SPI byte stream.

This is the rpi5 half of the Phase-5 link. It turns a (64, 256, 3) uint8 LINEAR
RGB frame into the exact 64 KB byte stream the RP2350 firmware expects in its
inactive framebuffer, so the firmware's PIO+DMA receive path drops it straight in
with zero CPU touch-up.

It is a 1:1 port of the firmware's `Display::render` (firmware/src/lib.rs) and
gamma LUT (firmware/src/lut.rs), and is proven **byte-identical** to the firmware
by tools/verify.py (which builds a golden frame with the firmware's own `libm`).
If you change the layout or gamma here, re-run that verifier and keep the
firmware + this file in lockstep.

Wire format (the "full display" = chain/row A over chain/row B):
  - Wall   : SPI_WIDTH x SPI_HEIGHT (256 x 64), SPI_BITDEPTH planes, gamma SPI_GAMMA
  - Input  : numpy uint8 (SPI_HEIGHT, SPI_WIDTH, 3), LINEAR RGB, C-contiguous.
             LINEAR because this packer owns gamma; the render readback must run
             at gamma 1.0 or color double-corrects.
  - Output : SPI_WIDTH*SPI_HEIGHT/2*SPI_BITDEPTH * 2 bytes (65536), u16 LE.

Cell index (matches firmware exactly):
    idx = addr_row*(W*B) + plane*W + (W-1-x)
  for wall row y (H = per-chain height = ROWS = 32):
    chain    = y // H                       # 0 = row A (top), 1 = row B (bottom)
    yc       = H-1 - (y % H)                # panel-mount vertical inversion
    addr_row = yc % (H//2)                  # 16 scan rows (1:16)
    half     = yc > (H//2 - 1)
    shift    = chain*6 + (3 if half else 0) # 3-bit RGB field in the u16 cell
Each wall row maps to a unique (addr_row, shift) -> no collisions.
"""

from __future__ import annotations

import numpy as np

from ..feed import config

# Per-chain geometry. W/2H is the wall; H is one chain's height (ROWS).
W = config.SPI_WIDTH            # 256
H = config.ROWS                 # 32 (per-chain height)
B = config.SPI_BITDEPTH         # 8
GAMMA = config.SPI_GAMMA        # 2.1
WALL_H = config.SPI_HEIGHT      # 64 (== 2*H)

FB_CELLS = W * H // 2 * B       # 32768 u16
FRAME_BYTES = FB_CELLS * 2      # 65536

assert WALL_H == 2 * H, "SPI_HEIGHT must be two chains tall (2*ROWS)"


def build_gamma_lut() -> np.ndarray:
    """CIE/gamma LUT, 256 -> 0..(2^B - 1), matching firmware/src/lut.rs.

    Firmware: value = roundf(max * powf(index/255, gamma)), max = (1<<B)-1,
    source_max = 255 (Rgb888) so remapped == index. Replicated in float32 with
    round-half-away (floor(x+0.5)) to mirror C `roundf`. Verified bit-identical
    to the firmware's libm output by tools/verify.py.
    """
    target_max = np.float32((1 << B) - 1)
    idx = np.arange(1 << B, dtype=np.float32)
    powed = np.power(idx / np.float32(255), np.float32(GAMMA), dtype=np.float32)
    return np.floor(target_max * powed + np.float32(0.5)).astype(np.uint16)


_LUT = build_gamma_lut()

# Per-row geometry, precomputed once.
_rows = np.arange(WALL_H)
_chain = _rows // H
_yc = H - 1 - (_rows % H)
_addr_row = _yc % (H // 2)
_half = _yc > (H // 2 - 1)
_shift = (_chain * 6 + np.where(_half, 3, 0)).astype(np.uint16)
_planes = np.arange(B, dtype=np.uint16)[:, None]


def pack(frame: np.ndarray, lut: np.ndarray = _LUT) -> bytes:
    """Pack a (64, 256, 3) uint8 LINEAR RGB frame into the 64 KB SPI byte stream."""
    if frame.shape != (WALL_H, W, 3) or frame.dtype != np.uint8:
        raise ValueError(
            f"expected ({WALL_H},{W},3) uint8, got {frame.shape} {frame.dtype}"
        )

    g = lut[frame]                       # gamma-correct each channel -> (64,256,3)
    pr, pg, pb = g[..., 0], g[..., 1], g[..., 2]

    fb3d = np.zeros((H // 2, B, W), dtype=np.uint16)
    for y in range(WALL_H):
        rb = (pr[y] >> _planes) & 1
        gb = (pg[y] >> _planes) & 1
        bb = (pb[y] >> _planes) & 1
        packed = ((bb << 2) | (gb << 1) | rb).astype(np.uint16) << _shift[y]
        fb3d[_addr_row[y], :, ::-1] |= packed   # col = W-1-x

    return fb3d.reshape(-1).astype("<u2").tobytes()
