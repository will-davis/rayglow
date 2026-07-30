"""Geometry, hardware, and network config for RayGLow.  Single source of truth.

Everything downstream derives geometry from here — never hardcode 256/64.
"""

# ----------------------------------------------------------------------------
# Panel geometry — the PHYSICAL panel grid.  These two are the knobs; everything
# downstream derives from them, so a new wall is a config bump here, not a code
# change.
#
# Wall v1 (retired): 4 wide x 2 tall of P6-3528-64x32 tiles = 256x64.
# Wall v2 (current): 6 wide x 4 tall of P4-2121-64x32 tiles = 384x128
# (1536x512 mm).  All 24 tiles run off ONE rp2350b on two HUB75 chains — the
# SRAM ceiling is 15 panels/chain (30 total), measured by linking phase6-parallel
# at rising widths, so 12/chain fits at 75% of the 512 KB pool.  See ROADMAP §5.
#
# STAGED BRING-UP: set PANEL_ROWS = 2 for the 6x2 (12-panel) step.  That gives
# one panel row per chain, which makes the serpentine fold the IDENTITY — the
# exact topology the v1 wall ran, just wider.  It isolates chain-length signal
# integrity before the fold is also in play.  Then set PANEL_ROWS = 4.
# ----------------------------------------------------------------------------
ROWS = 32                       # pixels per panel, vertical
COLS = 64                       # pixels per panel, horizontal
PANEL_COLS = 6                  # panels across the wall  (v1: 4)
PANEL_ROWS = 4                  # panels down the wall    (v1: 2)

# ----------------------------------------------------------------------------
# rp2350b link — the display is two HUB75 chains (custom HAT J2 = chain A, J3 =
# chain B), driven by the RP2350 firmware.  The rp2350b owns refresh timing;
# this host just renders and ships packed bit-plane frames over the link.
# Transport is the 4-lane RP1-PIO parallel bus by default (render/pio_out.py,
# firmware phase6-parallel) with 1-lane SPI as the fallback (render/spi_out.py,
# firmware phase5-spi); the byte stream is identical either way.
#
# A chain is ELECTRICALLY a single 32-row strip however many panels are on it,
# so a chain that spans more than one panel row is serpentined: across, U-turn,
# back.  That makes three widths, and conflating them is the classic bug here:
#   WALL_WIDTH   the logical picture the shader draws        (384)
#   CHAIN_WIDTH  one chain's electrical strip = firmware W   (768)
#   WALL_HEIGHT  the logical picture's height                (128)
# The firmware only ever sees CHAIN_WIDTH x (2*ROWS); render/hub75.to_chains is
# what folds the logical wall into that.  When PANEL_ROWS_PER_CHAIN == 1 the
# fold is the identity and CHAIN_WIDTH == WALL_WIDTH (the v1 arrangement).
# ----------------------------------------------------------------------------
PARALLEL_CHAINS = 2             # HUB75 chains in parallel — fixed by the HAT + firmware
assert PANEL_ROWS % PARALLEL_CHAINS == 0, \
    "PANEL_ROWS must divide evenly across the chains (each chain takes whole panel rows)"
PANEL_ROWS_PER_CHAIN = PANEL_ROWS // PARALLEL_CHAINS   # 2 (v1: 1)
CHAIN = PANEL_COLS * PANEL_ROWS_PER_CHAIN              # 12 panels daisy-chained per chain
                                       # MUST equal the firmware's PANELS_IN_CHAIN
                                       # (phase6_parallel.rs) — the link is a fixed-size
                                       # contract, so a mismatch desyncs it silently.
WALL_WIDTH = COLS * PANEL_COLS         # 384 — the logical wall the renderer draws
WALL_HEIGHT = ROWS * PANEL_ROWS        # 128
CHAIN_WIDTH = COLS * CHAIN             # 768 — one chain's electrical strip (firmware W)
BITDEPTH = 8                           # BCM planes — must equal firmware B (phase6_parallel.rs)
PACK_GAMMA = 2.1                       # CIE gamma the wall gets (mirrors firmware lut.rs).
                                       # Applied ONCE: baked into the GPU resolve pass by
                                       # default (packer gets LUT_IDENTITY), or by the
                                       # packer's LUT in --readback legacy (LINEAR readback)

# Physical-install orientation (rig-specific — see LOCAL-SETUP). A wall that
# takes HUB75 data on the RIGHT of each chain, with panels mounted inverted vs
# the firmware's scan convention, displays the image rotated 180deg from the
# rendered frame. Flip both axes before packing to compensate. Confirmed with:
#   python -m rayglow.spi_test --flipv --fliph
FLIP_H = True                           # left<->right (HUB75 input side)
FLIP_V = True                           # top<->bottom (panel mount vs scan order)
# FPGA/DPI rig (2026-07-22): wall physically rotated 180deg (HUB75 fed L->R from the
# front), so both axes flip. Baked into the GPU resolve pass -> zero latency, no hardware.
# Verify with an ASYMMETRIC shader (will-voidrainbow has 4-quadrant symmetry, hides it).

# ----------------------------------------------------------------------------
# Single-chain serpentine fallback (firmware: phase-experimental). The whole
# wall can be driven on ONE daisy-chain of all PANEL_COLS*PANEL_ROWS panels
# through a spare Adafruit HAT (used as a pure 3.3->5V level shifter, single
# output) — the bring-up rig before the custom two-chain HAT. Electrically that
# is one long strip carried on the engine's chain A (chain B left black); the
# renderer still draws the logical WALL_WIDTH x WALL_HEIGHT wall and
# render/hub75.to_single_chain folds it into the strip.
# Leave False for the two-chain rig.
# ----------------------------------------------------------------------------
SINGLE_CHAIN = False


def serpentine(panel_rows, panel_cols, chains, first_row_reversed=False):
    """Serpentine panel order per chain, + the per-panel-row 180deg rotate flags.

    Returns `(order, rotate)`:
      order[c][s] = (panel_row, panel_col) of the panel at strip position `s` of
                    chain `c`.  Strip position is in ELECTRICAL-STRIP x order —
                    the same coordinate space hub75.pack() consumes, so position
                    0 is the strip's x=0 end (pack applies the firmware's own
                    `W-1-x` mount inversion on top; see hub75.py).
      rotate[r]   = True if panel row `r` is mounted 180deg-rotated.

    Each chain takes `panel_rows // chains` whole panel rows and snakes through
    them: first row one way, U-turn, next row back.  A chain's U-turn rows are
    physically inverted (the panel is rotated so its connectors face the return
    run), hence `rotate`.

    `first_row_reversed` picks which end of the first row the strip starts at.
    That is a physical fact about where the HAT plugs in and CANNOT be derived
    from the desk — the two defaults below encode the two rigs' bench-confirmed
    conventions.  Confirm with `python -m rayglow.spi_test` (corner markers) and
    flip it if the image mirrors.
    """
    rows_per_chain = panel_rows // chains
    order, rotate = [], [False] * panel_rows
    for c in range(chains):
        strip = []
        for i in range(rows_per_chain):
            r = c * rows_per_chain + i
            # Alternate direction each row within the chain = the U-turn.
            ltr = (i % 2 == 0) != first_row_reversed
            cols = range(panel_cols) if ltr else range(panel_cols - 1, -1, -1)
            strip += [(r, x) for x in cols]
            rotate[r] = i % 2 == 1
        order.append(strip)
    return order, rotate


# Two-chain (custom HAT, production): chain A takes the top PANEL_ROWS_PER_CHAIN
# panel rows, chain B the bottom ones — so the chain split lands on the wall's
# horizontal midline, which is also the 12-panel-per-supply split (each rail's
# return current stays inside its own chain's domain — see POWER-AND-GROUNDING).
# first_row_reversed=False is the v1 wall's bench-confirmed convention: at one
# panel row per chain this generator is the IDENTITY, which is exactly how the
# v1 wall ran (no fold, FLIP_H/FLIP_V both False).
#
# Single-chain fallback: first_row_reversed=True — the spare Adafruit HAT plugs
# into the TOP-RIGHT panel, so the strip runs right->left across the top row,
# U-turns down, then left->right across the bottom row.
CHAIN_ORDER, ROW_ROTATE_180 = serpentine(
    PANEL_ROWS, PANEL_COLS,
    chains=1 if SINGLE_CHAIN else PARALLEL_CHAINS,
    first_row_reversed=SINGLE_CHAIN,
)

# ----------------------------------------------------------------------------
# Network (feature packets — see docs/design-history/project-milk-pi.md §5)
# ----------------------------------------------------------------------------
UDP_HOST = "0.0.0.0"            # listen on all interfaces
UDP_PORT = 5005                 # add a firewall rule if the feed crosses VLANs/subnets

# Renderer control plane (render/control.py): the live-dev push channel + media
# controls. TCP, not UDP — control must be reliable/ordered (a dropped "next" or
# a truncated shader push is a bug), the opposite of the lossy latest-wins feed.
# Bound on all interfaces so the desktop/nvim push straight to the Pi (same reach
# as the feed); firewall it if the LAN is untrusted, or set CONTROL_HOST to
# "127.0.0.1" and reach it over an ssh tunnel.
CONTROL_HOST = "0.0.0.0"
CONTROL_PORT = 5006
# Scratch dir the "push" command writes to (the tmp file that holds what's
# running). Kept out of the mutagen-synced tree so a push never fights the sync.
LIVE_DIR = "~/.cache/rayglow/live"

# ----------------------------------------------------------------------------
# Rendering
# ----------------------------------------------------------------------------
FALLBACK_AFTER = 0.5            # seconds without a packet before synth fallback kicks in
RENDER_CORE = 0                # pin the render thread here so frame pacing is steady
DEFAULT_SCALE = 2              # supersample factor when neither --scale nor a
                               # shader's `// rayglow: scale=` directive is set
                               # (1 = pixel-exact; 4 = smoother, ~16x GPU+readback)
