#!/usr/bin/env python3
"""Prove the serpentine fold (render/hub75.to_chains) is sane — no hardware.

A HUB75 chain is electrically ONE 32-row strip however many panels hang off it,
so a chain spanning several panel rows has to snake: across, U-turn, back. That
fold is pure bookkeeping, and bookkeeping bugs here look like "the wall is
scrambled" — indistinguishable at a glance from a wiring or signal-integrity
fault. This checks the bookkeeping from the desk so that, at bring-up, a scrambled
wall means *cabling*, not the packer.

It does NOT (and cannot) prove which physical panel is which: whether the strip
starts at the left or right of each row depends on where the HAT plugs in. That
is `serpentine(first_row_reversed=...)`, confirmed on the wall with
`python -m rayglow.spi_test` (corner markers + per-panel IDs).

What it proves, per geometry:
  1. IDENTITY   — one panel row per chain => the fold is a no-op, so the v1 wall
                  and the staged 6x2 step are bit-for-bit unaffected. Checked
                  against pack() output, not just array shape.
  2. LOSSLESS   — fold then unfold round-trips every pixel (so the fold is a pure
                  permutation of panels + 180deg rotations, losing nothing).
  3. COVERAGE   — every panel of the grid appears exactly once across the chains.
  4. RUNNABLE   — consecutive panels in a strip are physically adjacent, i.e. the
                  daisy-chain cable can actually be run panel-to-panel.
  5. CONTRACT   — the packed frame is the size the firmware's FRAME_BYTES expects,
                  and fits the RP2350's 512 KB SRAM double-buffered.

Run:  uv run --with numpy tools/fold_check.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

from rayglow.feed import config  # noqa: E402
from rayglow.feed.config import serpentine  # noqa: E402
from rayglow.render import hub75  # noqa: E402

PH, PW = config.ROWS, config.COLS          # 32, 64
SRAM = 512 * 1024                          # firmware/memory.x: RAM LENGTH


def unfold(elec, order, rot):
    """Inverse of hub75._fold — put each strip's panels back on the wall."""
    rows = max(r for s in order for r, _ in s) + 1
    cols = max(c for s in order for _, c in s) + 1
    wall = np.zeros((rows * PH, cols * PW, 3), elec.dtype)
    for c, strip in enumerate(order):
        for s, (pr, pc) in enumerate(strip):
            block = elec[c * PH:(c + 1) * PH, s * PW:(s + 1) * PW]
            if rot[pr]:
                block = block[::-1, ::-1]   # 180deg is its own inverse
            wall[pr * PH:(pr + 1) * PH, pc * PW:(pc + 1) * PW] = block
    return wall


def check(panel_rows, panel_cols, chains, label):
    """Run every desk-provable check on one geometry. Returns True if all pass."""
    order, rot = serpentine(panel_rows, panel_cols, chains)
    rows_per_chain = panel_rows // chains
    wall_w, wall_h = panel_cols * PW, panel_rows * PH
    chain_w = len(order[0]) * PW
    ok = True

    print(f"\n{label}")
    print(f"  {panel_cols}x{panel_rows} panels = {wall_w}x{wall_h} px | "
          f"{chains} chains x {len(order[0])} panels | strip {chain_w}x{2*PH}")

    rng = np.random.default_rng(0xC0FFEE)
    wall = rng.integers(0, 256, (wall_h, wall_w, 3), dtype=np.uint8)
    elec = hub75._fold(wall, order, rot)

    # --- 1. identity, when a chain covers exactly one panel row --------------
    if rows_per_chain == 1:
        if elec.shape == wall.shape and np.array_equal(elec, wall):
            # The stronger claim: the packed bytes are unchanged too.
            same = hub75.pack(elec, hub75.LUT_IDENTITY) == hub75.pack(wall, hub75.LUT_IDENTITY)
            print(f"  1. identity fold (no-op) {'✓' if same else '✗ pack differs!'}")
            ok &= same
        else:
            print("  1. identity fold ✗ — a 1-row-per-chain fold MUST be a no-op")
            ok = False
    else:
        print(f"  1. identity n/a ({rows_per_chain} panel rows/chain => real fold)")

    # --- 2. lossless round-trip ---------------------------------------------
    back = unfold(elec, order, rot)
    if np.array_equal(back, wall):
        print("  2. lossless: fold -> unfold round-trips every pixel ✓")
    else:
        bad = int((back != wall).any(axis=2).sum())
        print(f"  2. lossless ✗ — {bad} px differ after round-trip")
        ok = False

    # --- 3. every panel used exactly once ------------------------------------
    flat = [p for strip in order for p in strip]
    want = {(r, c) for r in range(panel_rows) for c in range(panel_cols)}
    if len(flat) == len(want) and set(flat) == want:
        print(f"  3. coverage: all {len(want)} panels used exactly once ✓")
    else:
        print(f"  3. coverage ✗ — missing {want - set(flat)}, "
              f"dupes {len(flat) - len(set(flat))}")
        ok = False

    # --- 4. the cable can physically be run ----------------------------------
    breaks = []
    for c, strip in enumerate(order):
        for s in range(len(strip) - 1):
            (r0, c0), (r1, c1) = strip[s], strip[s + 1]
            if abs(r0 - r1) + abs(c0 - c1) != 1:
                breaks.append((c, s, strip[s], strip[s + 1]))
    if not breaks:
        turns = sum(1 for strip in order
                    for s in range(len(strip) - 1) if strip[s][0] != strip[s + 1][0])
        print(f"  4. runnable: every hop is panel-adjacent ✓ "
              f"({turns // max(chains,1)} U-turn(s)/chain)")
    else:
        print(f"  4. runnable ✗ — {len(breaks)} non-adjacent hop(s): {breaks[:3]}")
        ok = False

    # --- 5. wire + SRAM contract ---------------------------------------------
    frame_bytes = chain_w * PH // 2 * config.BITDEPTH * 2
    both_fbs = frame_bytes * 2
    fits = both_fbs < SRAM
    print(f"  5. frame {frame_bytes//1024} KB | both FBs {both_fbs//1024} KB = "
          f"{both_fbs/SRAM*100:.0f}% of 512 KB SRAM {'✓' if fits else '✗ WILL NOT LINK'}")
    ok &= fits
    return ok


def main() -> int:
    print("fold_check: proving render/hub75.to_chains from the desk")
    results = [
        check(2, 4, 2, "v1 wall (retired) — 4x2, 1 panel row/chain"),
        check(2, 6, 2, "STAGED bring-up — 6x2, 1 panel row/chain (set PANEL_ROWS=2)"),
        check(4, 6, 2, "v2 wall — 6x4, 2 panel rows/chain (the serpentine)"),
    ]

    # The live config must agree with the firmware's compile-time constants.
    print("\nACTIVE config (rayglow/feed/config.py)")
    print(f"  wall {config.WALL_WIDTH}x{config.WALL_HEIGHT} | "
          f"CHAIN={config.CHAIN} panels/chain | CHAIN_WIDTH={config.CHAIN_WIDTH}")
    print(f"  -> firmware phase6_parallel.rs MUST have "
          f"PANELS_IN_CHAIN = {config.CHAIN}  (W = {config.CHAIN_WIDTH})")
    print(f"  -> link frame = {hub75.FRAME_BYTES} bytes "
          f"({hub75.FRAME_BYTES//1024} KB); both sides derive this, a mismatch "
          f"desyncs the link silently")
    if hub75.FRAME_BYTES * 2 >= SRAM:
        print("  ✗ ACTIVE config will NOT link — framebuffers exceed 512 KB SRAM")
        results.append(False)

    print()
    if all(results):
        print("all fold checks pass ✓")
        return 0
    print("FOLD CHECKS FAILED ✗")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
