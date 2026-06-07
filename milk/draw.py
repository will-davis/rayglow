"""Drawing primitives — where audio energy enters the feedback loop.

All draw into the float32 0..1 buffer.  Additive drawing means hits brighten
trails which then decay away — the core MilkDrop look.  Values may exceed 1.0
here; clipping happens once, at composite time.

Lines are fat (thickness>=2) by default: at P6 pitch a 1px line disappears.
"""
import cv2
import numpy as np


def waveform(buf, wave, color, thickness=2, amp=0.4, y_center=0.5,
             additive=True, antialias=True):
    """Draw the waveform window as a polyline spanning the full width.

    wave:     float array (any length), ±1.0
    color:    (r,g,b) floats, 0..1 (may exceed 1 for extra punch pre-decay)
    amp:      vertical extent as fraction of buffer height (wave=+1 -> y_center-amp)
    y_center: 0..1, vertical placement of the centerline
    """
    h, w = buf.shape[:2]
    n = len(wave)
    xs = np.linspace(0, w - 1, n, dtype=np.float32)
    ys = (y_center - wave * amp) * (h - 1)          # +wave is up
    np.clip(ys, 0, h - 1, out=ys)
    pts = np.stack([xs, ys], axis=1).astype(np.int32).reshape(-1, 1, 2)

    line_type = cv2.LINE_AA if antialias else cv2.LINE_8
    if additive:
        stamp = np.zeros_like(buf)
        cv2.polylines(stamp, [pts], False, color, thickness, line_type)
        buf += stamp
    else:
        cv2.polylines(buf, [pts], False, color, thickness, line_type)


def dot(buf, x, y, color, radius=2, additive=True):
    """Filled circle at normalized (x,y), 0..1.  Future shape/beat-flash hook."""
    h, w = buf.shape[:2]
    center = (int(x * (w - 1)), int(y * (h - 1)))
    if additive:
        stamp = np.zeros_like(buf)
        cv2.circle(stamp, center, radius, color, -1, cv2.LINE_AA)
        buf += stamp
    else:
        cv2.circle(buf, center, radius, color, -1, cv2.LINE_AA)
