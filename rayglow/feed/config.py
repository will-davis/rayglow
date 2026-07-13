"""Geometry, hardware, and network config for RayGLow.  Single source of truth.

Everything downstream derives geometry from here — never hardcode 256/64.
"""

# ----------------------------------------------------------------------------
# Panel geometry.  CHAIN is the one knob to change if a panel is added/removed.
# ----------------------------------------------------------------------------
ROWS = 32                       # pixels per panel, vertical
COLS = 64                       # pixels per panel, horizontal
CHAIN = 4                       # daisy-chained panels per chain (4 = 256 wide)

# ----------------------------------------------------------------------------
# rp2350b link — the display is two HUB75 chains, row A stacked over row B,
# driven by the RP2350 firmware.  The rp2350b owns refresh timing; this host
# just renders and ships packed bit-plane frames over the link.  Transport is
# the 4-lane RP1-PIO parallel bus by default (render/pio_out.py, firmware
# phase6-parallel) with 1-lane SPI as the fallback (render/spi_out.py, firmware
# phase5-spi); the byte stream is identical either way.
# ----------------------------------------------------------------------------
PARALLEL_CHAINS = 2                    # HUB75 chains driven in parallel by the rp2350b
WALL_WIDTH = COLS * CHAIN              # 256 (same width as one chain)
WALL_HEIGHT = ROWS * PARALLEL_CHAINS   # 64 (two stacked 32-row chains)
BITDEPTH = 8                           # BCM planes — must equal firmware B (phase6_parallel.rs)
PACK_GAMMA = 2.1                       # CIE gamma the packer applies (mirrors firmware lut.rs) —
                                       # the render readback must stay LINEAR (gamma 1.0)

# Physical-install orientation (rig-specific — see LOCAL-SETUP). A wall that
# takes HUB75 data on the RIGHT of each chain, with panels mounted inverted vs
# the firmware's scan convention, displays the image rotated 180deg from the
# rendered frame. Flip both axes before packing to compensate. Confirmed with:
#   python -m rayglow.spi_test --flipv --fliph
FLIP_H = False                          # left<->right (HUB75 input side)
FLIP_V = False                          # top<->bottom (panel mount vs scan order)

# ----------------------------------------------------------------------------
# Single-chain serpentine fallback (firmware: phase-experimental). The whole
# wall can be driven on ONE daisy-chain of all CHAIN*PARALLEL_CHAINS panels
# through a spare Adafruit HAT (used as a pure 3.3->5V level shifter, single
# output) — the bring-up rig before the custom two-chain HAT. Electrically that
# is a 512-wide strip carried on the engine's chain A (chain B left black); the
# renderer still draws the logical WALL_WIDTH x WALL_HEIGHT wall and
# render/hub75.to_single_chain folds it into the strip. Frame doubles to 128 KB.
# Leave False for the two-chain rig.
# ----------------------------------------------------------------------------
SINGLE_CHAIN = False
# Daisy-chain order of the panels as (panel_row, panel_col); (0,0) = top-left.
# Default: the HAT plugs into the TOP-RIGHT panel; signal runs right->left across
# the top row, U-turns down, then left->right across the bottom row.
CHAIN_ORDER = [(0, 3), (0, 2), (0, 1), (0, 0)]
#CHAIN_ORDER = [(0, 3), (0, 2), (0, 1), (0, 0), (1, 0), (1, 1), (1, 2), (1, 3)]
## Per panel-row 180deg rotation — the serpentine U-turn physically inverts the
# bottom row, so flip its H and V. Index = panel_row. Confirm against the
# rayglow.spi_test orientation pattern before trusting it.
ROW_ROTATE_180 = [False, True]

# ----------------------------------------------------------------------------
# Network (feature packets — see docs/design-history/project-milk-pi.md §5)
# ----------------------------------------------------------------------------
UDP_HOST = "0.0.0.0"            # listen on all interfaces
UDP_PORT = 5005                 # add a firewall rule if the feed crosses VLANs/subnets

# ----------------------------------------------------------------------------
# Rendering
# ----------------------------------------------------------------------------
FALLBACK_AFTER = 0.5            # seconds without a packet before synth fallback kicks in
RENDER_CORE = 0                # pin the render thread here so frame pacing is steady
