"""Preset 1 — "tunnel of light trails" (the project's first milestone).

Decay + center-zoom remap + bass-driven waveform.  Bass hits push the zoom,
so every beat launches the waveform outward into concentric trails.
"""
import math

# ---- tunables ---------------------------------------------------------------
DECAY = 0.94          # trail length: lower = shorter trails
ZOOM_BASE = 1.025     # constant outward drift (>1 = tunnel flows outward)
ZOOM_BASS = 0.06      # extra zoom per unit of bass-above-average
HUE_SPEED = 0.07      # waveform color cycle, rotations/second
WAVE_AMP_BASE = 0.22  # waveform height at quiet passages (fraction of panel)
WAVE_AMP_BASS = 0.18  # extra height on bass hits
BRIGHT = 1.0          # waveform brightness (can exceed 1.0 for punch)
# -----------------------------------------------------------------------------


def _hue_rgb(h):
    """Cheap smooth hue -> rgb (0..1), no colorsys import needed per frame."""
    return (0.5 + 0.5 * math.cos(6.2832 * h),
            0.5 + 0.5 * math.cos(6.2832 * (h - 1.0 / 3.0)),
            0.5 + 0.5 * math.cos(6.2832 * (h - 2.0 / 3.0)))


def tunnel(f):
    bass_hit = max(0.0, f.bass - 1.0)            # 0 at average, ~1 on big hits
    r, g, b = _hue_rgb(f.t * HUE_SPEED)
    treb_glow = 0.7 + 0.3 * min(2.0, max(0.0, f.treb))
    return {
        "decay": DECAY,
        "zoom": ZOOM_BASE + ZOOM_BASS * bass_hit,
        "wave_color": (r * BRIGHT * treb_glow,
                       g * BRIGHT * treb_glow,
                       b * BRIGHT * treb_glow),
        "wave_amp": WAVE_AMP_BASE + WAVE_AMP_BASS * bass_hit,
    }
