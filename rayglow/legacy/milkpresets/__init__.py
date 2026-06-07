"""Preset registry + steering-parameter defaults.

A preset is a pure function: FeatureState -> dict of steering overrides.
This is MilkDrop's "per-frame equation" slot, hardcoded in Python for now.
The engine merges the returned dict over DEFAULTS, so presets stay terse and
only mention what they steer.
"""

DEFAULTS = {
    # feedback
    "decay": 0.96,            # buffer multiply per frame (0.90 punchy .. 0.99 dreamy)
    "border": "constant",     # constant | wrap | reflect | replicate

    # warp-stage steering (see warp.compute_maps)
    "zoom": 1.0,              # >1 zooms in (content flows outward)
    "zoom_exp": 1.0,          # radius-shaping of zoom
    "rot": 0.0,               # radians/frame-step of UV rotation
    "warp": 0.0,              # drunken-sine magnitude (typ. 0..2)
    "warp_anim_speed": 1.0,
    "warp_scale": 1.0,        # spatial scale of the warp wobble
    "cx": 0.5, "cy": 0.5,     # zoom/rot/stretch center (UV space)
    "dx": 0.0, "dy": 0.0,     # translation per frame (UV space)
    "sx": 1.0, "sy": 1.0,     # stretch

    # waveform draw
    "wave_color": (1.0, 1.0, 1.0),
    "wave_amp": 0.35,         # fraction of height
    "wave_y": 0.5,
    "wave_thickness": 2,

    # filled by the engine, not presets:
    "time": 0.0,
}

from .tunnel import tunnel
from .wobble import wobble

PRESETS = {
    "tunnel": tunnel,
    "wobble": wobble,
}
