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
# rp2350b SPI link — the display is two HUB75 chains, row A stacked over row B,
# driven by the RP2350 firmware.  The rp2350b owns refresh timing; this host
# just renders and ships packed bit-plane frames over SPI (see render/spi_out).
# ----------------------------------------------------------------------------
SPI_PARALLEL = 2                       # two parallel chains (rp2350b drives both)
SPI_WIDTH = COLS * CHAIN               # 256 (same width as one chain)
SPI_HEIGHT = ROWS * SPI_PARALLEL       # 64 (two stacked 32-row chains)
SPI_BITDEPTH = 8                       # BCM planes — must equal firmware B (phase5_spi.rs)
SPI_GAMMA = 2.1                        # firmware CIE LUT exponent (lut.rs) — packer owns gamma,
                                       # so the render readback must stay LINEAR (gamma 1.0)

# Physical-install orientation (rig-specific — see LOCAL-SETUP). A wall that
# takes HUB75 data on the RIGHT of each chain, with panels mounted inverted vs
# the firmware's scan convention, displays the image rotated 180deg from the
# rendered frame. Flip both axes before packing to compensate. Confirmed with:
#   python -m rayglow.spi_test --flipv --fliph
SPI_FLIP_H = True                      # left<->right (HUB75 input side)
SPI_FLIP_V = True                      # top<->bottom (panel mount vs scan order)

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
