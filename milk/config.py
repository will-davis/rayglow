"""Geometry, hardware, and network config for Milk-Pi.  Single source of truth.

Everything downstream derives geometry from here — never hardcode 256/32.
"""

# ----------------------------------------------------------------------------
# Panel geometry.  CHAIN is the one knob to change if the 5th panel returns.
# ----------------------------------------------------------------------------
ROWS = 32                       # pixels per panel, vertical
COLS = 64                       # pixels per panel, horizontal
CHAIN = 4                       # daisy-chained panels (currently 4 = 256 wide)
PARALLEL = 1

WIDTH = COLS * CHAIN            # logical framebuffer width  (256)
HEIGHT = ROWS * PARALLEL        # logical framebuffer height (32)

# ----------------------------------------------------------------------------
# Network (feature packets, project-milk-pi.md §5)
# ----------------------------------------------------------------------------
UDP_HOST = "0.0.0.0"            # listen on all interfaces
UDP_PORT = 5005                 # note in OPNsense firewall rule if crossing VLANs

# ----------------------------------------------------------------------------
# Rendering
# ----------------------------------------------------------------------------
GAMMA = 1.2                     # composite exponent: <1 lifts faint trails, >1 deepens blacks
WAVE_FIT = 1.0                  # .milk custom-wave vertical mapping: 1.0 squashes the
                                # preset's ~square canvas onto the panel (everything
                                # visible, vertically compressed); 0.0 = MilkDrop-faithful
                                # (only the center 1/8 horizontal slice shows at 8:1)
FALLBACK_AFTER = 0.5            # seconds without a packet before synth fallback kicks in
RENDER_CORE = 0                 # pin render thread here; hzeller GPIO thread owns core 3


def matrix_options():
    """Known-good RGBMatrixOptions for the rig (June 2026 tuning).

    Imported lazily so headless mode never touches rgbmatrix.
    """
    from rgbmatrix import RGBMatrixOptions

    options = RGBMatrixOptions()
    options.rows = ROWS
    options.cols = COLS
    options.chain_length = CHAIN
    options.parallel = PARALLEL
    options.disable_hardware_pulsing = 0           # snd_bcm2835 is blacklisted
    options.gpio_slowdown = 5                      # tuned: 5 + lsb 130 kills panel-1 artifact
    options.brightness = 100
    options.pwm_bits = 10
    options.pwm_lsb_nanoseconds = 130
    options.hardware_mapping = "adafruit-hat-pwm"  # GPIO4->GPIO18 jumper installed
    return options
