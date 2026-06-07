"""Preset 2 — rot/dx/warp wobble.  Proves the steering interface generalizes
beyond pure zoom: rotation sways with time, mid-energy drifts the field
sideways, and the drunken-sine warp smears everything organically.
"""
import math

# ---- tunables ---------------------------------------------------------------
DECAY = 0.95
ZOOM = 1.012          # gentle outward drift under the wobble
ROT_AMP = 0.035       # radians of rotation sway
ROT_SPEED = 0.4       # sway rate (rad/s of the LFO)
DX_MID = 0.006        # sideways drift per unit of mid-above-average
WARP = 0.8            # drunken-sine magnitude
WARP_ANIM_SPEED = 1.0
WARP_SCALE = 1.5      # bigger = broader, lazier wobble
HUE_SPEED = 0.05
# -----------------------------------------------------------------------------


def _hue_rgb(h):
    return (0.5 + 0.5 * math.cos(6.2832 * h),
            0.5 + 0.5 * math.cos(6.2832 * (h - 1.0 / 3.0)),
            0.5 + 0.5 * math.cos(6.2832 * (h - 2.0 / 3.0)))


def wobble(f):
    bass_hit = max(0.0, f.bass - 1.0)
    r, g, b = _hue_rgb(f.t * HUE_SPEED + 0.5)
    return {
        "decay": DECAY,
        "zoom": ZOOM + 0.03 * bass_hit,
        "rot": ROT_AMP * math.sin(f.t * ROT_SPEED),
        "dx": DX_MID * (f.mid - 1.0),
        "warp": WARP,
        "warp_anim_speed": WARP_ANIM_SPEED,
        "warp_scale": WARP_SCALE,
        "border": "wrap",                       # smeared field wraps seamlessly
        "wave_color": (r, g, b),
        "wave_amp": 0.2 + 0.15 * bass_hit,
    }
