"""Bit-plane packer: full-display RGB frame -> rp2350b link byte stream.

This is the rpi5 half of the Pi->RP2350 link (both transports: the 4-lane
parallel PIO bus and the SPI fallback carry this identical stream). It turns a
(64, CHAIN_WIDTH, 3) uint8 LINEAR RGB frame into the exact byte stream the RP2350
firmware expects in its inactive framebuffer, so the firmware's PIO+DMA receive
path drops it straight in with zero CPU touch-up.

NOTE the input is the ELECTRICAL frame (chain A's strip stacked on chain B's),
not the logical wall — `to_chains()` folds one into the other. They are the same
array only when each chain covers a single panel row. The firmware knows nothing
about the wall's shape; it just scans two CHAIN_WIDTH-wide strips.

It is a 1:1 port of the firmware's `Display::render` (firmware/src/lib.rs) and
gamma LUT (firmware/src/lut.rs), and is proven **byte-identical** to the firmware
by tools/verify.py (which builds a golden frame with the firmware's own `libm`).
If you change the layout or gamma here, re-run that verifier and keep the
firmware + this file in lockstep.

Wire format (the "full display" = chain A's strip over chain B's):
  - Strip  : CHAIN_WIDTH x 2*ROWS (768 x 64), BITDEPTH planes, gamma PACK_GAMMA
  - Input  : numpy uint8 (2*ROWS, CHAIN_WIDTH, 3), C-contiguous — the output of
             to_chains(). With the default LUT the input must be LINEAR RGB (the
             LUT applies gamma); frames that the GPU resolve pass already
             gamma-corrected are packed with LUT_IDENTITY instead — gamma is
             applied exactly once either way.
  - Output : CHAIN_WIDTH*ROWS/2*BITDEPTH * 2 bytes (196608 at 12 panels/chain;
             65536 at 4), u16 LE.

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

# Per-chain geometry. W is one chain's ELECTRICAL strip width (the firmware's W),
# NOT the logical wall width — a chain that spans two panel rows is twice as wide
# electrically as the wall is (768 vs 384). H is one chain's height (ROWS).
W = config.CHAIN_WIDTH          # 768 (one chain's strip; pack() infers width per-frame)
H = config.ROWS                 # 32 (per-chain height)
B = config.BITDEPTH             # 8
GAMMA = config.PACK_GAMMA       # 2.1
# The firmware framebuffer is ALWAYS two chains tall (chain B is idle/black in
# single-chain mode), so the packed frame is 2*H rows regardless of the logical
# render height (config.WALL_HEIGHT). Decoupled from WALL_HEIGHT so a single-chain
# rig can render a shorter wall without tripping a two-chain invariant.
WALL_H = 2 * H                  # 64 — packed-frame height (both chains)

FB_CELLS = W * H // 2 * B       # 98304 u16 (two-chain reference; pack sizes per-frame)
FRAME_BYTES = FB_CELLS * 2      # 196608 (192 KB at 12 panels/chain; 64 KB at 4)

# The two-chain u16 engine is hardwired to exactly 2 chains (12 RGB bits over
# GP0-11 — see firmware/src/lib.rs), so the packed frame is always 2*ROWS tall.
# The logical wall can be taller than that (PANEL_ROWS > PARALLEL_CHAINS): that is
# what to_chains() folds away, which is why this is no longer a WALL_HEIGHT check.
if not config.SINGLE_CHAIN:
    assert config.PARALLEL_CHAINS * H == WALL_H, \
        "the two-chain firmware engine drives exactly PARALLEL_CHAINS=2 chains"


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

# For frames that arrive ALREADY gamma-corrected (the GPU resolve pass bakes
# pow(x, PACK_GAMMA) into its shader): identity LUT = the packer's lookup
# becomes a pure uint8->uint16 widening, and gamma is not applied twice.
# The default _LUT path (LINEAR input) is unchanged and stays what
# tools/verify.py proves byte-identical to the firmware.
LUT_IDENTITY = np.arange(1 << B, dtype=np.uint16)

# Per-row geometry, precomputed once.
_rows = np.arange(WALL_H)
_chain = _rows // H
_yc = H - 1 - (_rows % H)
_addr_row = _yc % (H // 2)
_half = _yc > (H // 2 - 1)
_shift = (_chain * 6 + np.where(_half, 3, 0)).astype(np.uint16)
_planes = np.arange(B, dtype=np.uint16)[:, None]

# Group wall rows by addr_row once. Each addr_row is fed by WALL_H/(H/2) rows whose
# bit-shifts are disjoint (unique (addr_row, shift) pairs), so pack() can OR-combine
# them with a single vectorized reduce instead of a per-row Python loop. Stable sort
# keeps the grouping order deterministic; the assert pins the uniform-grouping invariant.
_order = np.argsort(_addr_row, kind="stable")
_n_per = WALL_H // (H // 2)
assert np.array_equal(
    _addr_row[_order].reshape(H // 2, _n_per),
    np.broadcast_to(np.arange(H // 2)[:, None], (H // 2, _n_per)),
), "pack(): addr_row grouping is not uniform"


def pack(frame: np.ndarray, lut: np.ndarray = _LUT) -> bytes:
    """Pack a (WALL_H, w, 3) uint8 LINEAR RGB frame into the TWO-CHAIN u16 stream.

    This is the production two-chain packer (12-bit u16 cell, both chains). The
    chain width `w` is read from the frame; the per-row geometry (_shift/_addr_row)
    depends only on height, and the firmware cell index `addr_row*(w*B) + plane*w +
    (w-1-x)` is width-parametric. Output is `w*H/2*B*2` bytes (64 KB at w=256).
    For the single-chain (one HUB75 chain) rig use `pack_single`.
    """
    if (frame.ndim != 3 or frame.shape[0] != WALL_H or frame.shape[2] != 3
            or frame.dtype != np.uint8):
        raise ValueError(
            f"expected (WALL_H={WALL_H}, w, 3) uint8, got {frame.shape} {frame.dtype}"
        )
    w = frame.shape[1]                   # chain width (256 two-chain)

    g = lut[frame]                       # gamma-correct each channel -> (WALL_H,w,3)
    # Bit-planes for every row at once -> (WALL_H, B, w). _planes[None] is (1,B,1).
    pr, pg, pb = g[..., 0][:, None, :], g[..., 1][:, None, :], g[..., 2][:, None, :]
    rb = (pr >> _planes[None]) & 1
    gb = (pg >> _planes[None]) & 1
    bb = (pb >> _planes[None]) & 1
    packed = ((bb << 2) | (gb << 1) | rb).astype(np.uint16) << _shift[:, None, None]
    packed = packed[:, :, ::-1]          # col = w-1-x, applied once

    # OR-combine the rows that share each addr_row (grouped by _order) in one reduce.
    fb3d = np.bitwise_or.reduce(packed[_order].reshape(H // 2, _n_per, B, w), axis=1)
    return fb3d.reshape(-1).astype("<u2").tobytes()


# Single-chain (u8) per-row geometry: H rows, ONE chain (no chain offset). Mirrors
# firmware/src/single.rs Display1::render.
_yc_s = H - 1 - np.arange(H)
_addr_s = _yc_s % (H // 2)
_shift_s = np.where(_yc_s > (H // 2 - 1), 3, 0).astype(np.uint8)
_planes_s = np.arange(B, dtype=np.uint8)[:, None]

# Same addr_row grouping as the two-chain packer, for pack_single's vectorized reduce.
_order_s = np.argsort(_addr_s, kind="stable")
_n_per_s = H // (H // 2)
assert np.array_equal(
    _addr_s[_order_s].reshape(H // 2, _n_per_s),
    np.broadcast_to(np.arange(H // 2)[:, None], (H // 2, _n_per_s)),
), "pack_single(): addr_row grouping is not uniform"


def pack_single(frame: np.ndarray, lut: np.ndarray = _LUT) -> bytes:
    """Pack an (H, w, 3) uint8 LINEAR strip into the SINGLE-CHAIN u8 byte stream.

    One HUB75 chain → one u8 cell per (col, plane, addr-row): 6 RGB bits, no chain
    B, so the frame is HALF the two-chain size — `w*H/2*B` bytes (64 KB at w=512 /
    8 panels, 32 KB at w=256 / 4). Input is the H-tall electrical strip from
    `to_single_chain`. 1:1 port of `single::Display1::render`; proven byte-
    identical to it by tools/verify.py.
    """
    if (frame.ndim != 3 or frame.shape[0] != H or frame.shape[2] != 3
            or frame.dtype != np.uint8):
        raise ValueError(
            f"expected (H={H}, w, 3) uint8, got {frame.shape} {frame.dtype}"
        )
    w = frame.shape[1]

    g = lut[frame]
    pr, pg, pb = g[..., 0][:, None, :], g[..., 1][:, None, :], g[..., 2][:, None, :]
    rb = (pr >> _planes_s[None]) & 1
    gb = (pg >> _planes_s[None]) & 1
    bb = (pb >> _planes_s[None]) & 1
    packed = ((bb << 2) | (gb << 1) | rb).astype(np.uint8) << _shift_s[:, None, None]
    packed = packed[:, :, ::-1]                 # col = w-1-x, applied once

    fb3d = np.bitwise_or.reduce(packed[_order_s].reshape(H // 2, _n_per_s, B, w), axis=1)
    return fb3d.reshape(-1).tobytes()           # u8, contiguous


def _fold(frame: np.ndarray, order, rot) -> np.ndarray:
    """Lay the logical wall's panels out into one strip per chain.

    Shared core of `to_chains` (two-chain u16 rig) and `to_single_chain` (the
    one-chain u8 fallback) — the fold is the same operation either way; only the
    chain count and the packer that consumes it differ.

    `order[c][s]` = the (panel_row, panel_col) sitting at strip position `s` of
    chain `c`; panel rows flagged in `rot` are rotated 180deg (the serpentine
    U-turn inverts them). Output stacks the chains vertically — chain 0 on top —
    which is exactly the (2*ROWS, chain_width, 3) frame `pack()` expects, and the
    (ROWS, strip_width, 3) one `pack_single()` expects when there is one chain.
    """
    ph, pw = config.ROWS, config.COLS              # 32, 64 (panel height/width)
    # The order must fit inside the rendered frame (and have a rotate flag for
    # every panel row it references) — catch a config mismatch with a clear
    # message instead of a numpy broadcast error.
    flat = [p for strip in order for p in strip]
    need_rows = max(pr for pr, _ in flat) + 1
    need_cols = max(pc for _, pc in flat) + 1
    if frame.shape[0] < need_rows * ph or frame.shape[1] < need_cols * pw:
        raise ValueError(
            f"CHAIN_ORDER spans a {need_cols*pw}x{need_rows*ph} panel grid but "
            f"the render is {frame.shape[1]}x{frame.shape[0]} — match PANEL_COLS/"
            f"PANEL_ROWS to the panels you actually chained"
        )
    if len(rot) < need_rows:
        raise ValueError(f"ROW_ROTATE_180 needs >= {need_rows} entries")
    n = len(order[0])
    if any(len(s) != n for s in order):
        raise ValueError("every chain must carry the same number of panels")

    elec = np.zeros((len(order) * ph, n * pw, 3), dtype=frame.dtype)
    for c, strip in enumerate(order):
        for s, (prow, pcol) in enumerate(strip):
            block = frame[prow * ph:(prow + 1) * ph, pcol * pw:(pcol + 1) * pw]
            if rot[prow]:
                block = block[::-1, ::-1]          # 180deg = H flip + V flip
            elec[c * ph:(c + 1) * ph, s * pw:(s + 1) * pw] = block
    return np.ascontiguousarray(elec)


# Is the configured fold a no-op (each chain covers exactly one panel row, laid
# left-to-right, unrotated)? That is the v1 wall and the staged 6x2 step, where
# the logical wall IS the electrical frame. Precomputed so to_chains() can hand
# the frame straight back instead of memcpy'ing it into an identical array.
_FOLD_IS_IDENTITY = (
    not config.SINGLE_CHAIN
    and not any(config.ROW_ROTATE_180)
    and config.CHAIN_ORDER == [[(c, x) for x in range(config.PANEL_COLS)]
                               for c in range(config.PARALLEL_CHAINS)]
)


def to_chains(frame: np.ndarray) -> np.ndarray:
    """Fold the logical wall into the TWO chains' electrical strips.

    A HUB75 chain is electrically one ROWS-tall strip no matter how many panels
    hang off it, so a chain spanning two panel rows is serpentined: across the
    first row, U-turn, back along the second. This converts the logical picture
    into what the firmware actually scans out.

    Input : (WALL_HEIGHT, WALL_WIDTH, 3) uint8 — the logical wall (384x128).
    Output: (2*ROWS, CHAIN_WIDTH, 3) uint8 — chain A's 768-wide strip stacked on
            chain B's; feed straight to `pack()`, which infers the width.

    When each chain covers ONE panel row (PANEL_ROWS_PER_CHAIN == 1, i.e. the v1
    wall and the staged 6x2 step) this is the IDENTITY — same array, unchanged —
    so the no-fold rigs are bit-for-bit unaffected. `tools/fold_check.py` pins
    that, plus losslessness for the folded case.

    The order/rotation depend on which panel the HAT plugs into and the cabling;
    confirm with `python -m rayglow.spi_test` (the corner-marker pattern).
    """
    if _FOLD_IS_IDENTITY:
        return frame
    return _fold(frame, config.CHAIN_ORDER, config.ROW_ROTATE_180)


def to_single_chain(frame: np.ndarray) -> np.ndarray:
    """Fold the logical wall into the ONE-chain electrical strip (the u8 engine,
    firmware `phase-experimental` / default `phase6-parallel`).

    All PANEL_COLS*PANEL_ROWS panels run on ONE daisy-chain (the spare Adafruit
    HAT, single output). Electrically that is a (ROWS, COLS*N) strip on chain A
    (chain B unused). Requires `config.SINGLE_CHAIN = True`, which is what makes
    `config.CHAIN_ORDER` a single strip covering every panel row.

    Input : (render_h, render_w, 3) uint8 — the logical wall.
    Output: (ROWS, COLS*N, 3) uint8 — ONE chain tall (the u8 engine has no chain
            B); feed straight to `pack_single`. Width = N panels × COLS.
    """
    if len(config.CHAIN_ORDER) != 1:
        raise ValueError(
            f"to_single_chain needs a 1-chain CHAIN_ORDER, got "
            f"{len(config.CHAIN_ORDER)} — set config.SINGLE_CHAIN = True"
        )
    return _fold(frame, config.CHAIN_ORDER, config.ROW_ROTATE_180)
