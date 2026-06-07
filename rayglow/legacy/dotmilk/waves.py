"""Main-wave draw modes, borders, darken-center — port of DrawWave
(milkdropfs.cpp:2682-3161) adapted to a 128-sample mono wave and 256x32 px.

Coordinates here are D3D-normalized: x,y in [-1,1].  fL/fR stereo channels
are emulated from the mono packet wave (fR = slightly shifted copy, matching
the original's pervasive fL[i]/fR[i+offset] decorrelation trick).

MilkDrop alpha-blends; we approximate: additive waves accumulate into the
float buffer, non-additive draw scaled color directly.  Alpha multiplies the
color either way (no destination blend) — close enough at LED resolution.
"""
import math

import cv2
import numpy as np

from ...feed import config
from ..warp import ASPECT_X, ASPECT_Y

W, H = config.WIDTH, config.HEIGHT


def _f(ns, key, default=0.0):
    try:
        v = float(ns[key])
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default


def _to_px(x, y):
    """D3D normalized coords -> integer pixel points (N,1,2) for cv2."""
    px = (np.asarray(x) + 1.0) * 0.5 * (W - 1)
    py = (np.asarray(y) + 1.0) * 0.5 * (H - 1)
    pts = np.stack([np.clip(px, -10 * W, 10 * W),
                    np.clip(py, -10 * H, 10 * H)], axis=-1)
    return pts.astype(np.int32).reshape(-1, 1, 2)


def _stamp_lines(buf, point_lists, color, thickness, additive, dots):
    color = tuple(float(np.clip(c, 0.0, 4.0)) for c in color)
    if max(color) <= 0.001:
        return
    target = np.zeros_like(buf) if additive else buf
    for pts in point_lists:
        if len(pts) < 2:
            continue
        if dots:
            for q in pts[::2]:
                cv2.circle(target, tuple(int(c) for c in q[0]), 0, color, -1)
        else:
            cv2.polylines(target, [pts], False, color, thickness, cv2.LINE_AA)
    if additive:
        buf += target


def _smooth(wave, amount):
    """MilkDrop-style recursive smoothing of the sample window."""
    if amount <= 0.0:
        return wave
    out = np.empty_like(wave)
    out[0] = wave[0]
    a = float(amount)
    for i in range(1, len(wave)):
        out[i] = out[i - 1] * a + wave[i] * (1.0 - a)
    return out


def wave_points(mode, fL, fR, t, ns, preset, features):
    """Return a list of (x_array, y_array) polylines in D3D coords."""
    n = len(fL)
    mys = _f(ns, "wave_mystery")
    # fWaveParam2 folding (:2784) for modes 0/1/4
    if mode in (0, 1, 4) and (mys < -1 or mys > 1):
        mys = mys * 0.5 + 0.5
        mys -= math.floor(mys)
        mys = abs(mys) * 2 - 1
    wx = _f(ns, "wave_x", 0.5) * 2.0 - 1.0
    wy = _f(ns, "wave_y", 0.5) * 2.0 - 1.0
    off = max(1, n // 15)              # the original's fL[i+32]/480 ≈ 1/15 shift

    if mode == 0:                      # circular wave (:2803)
        m = n // 2
        i = np.arange(m)
        rad = 0.5 + 0.4 * fR[i + m // 2] + mys
        ang = i / (m - 1) * 6.28 + t * 0.2
        x = rad * np.cos(ang) * ASPECT_Y + wx
        y = rad * np.sin(ang) * ASPECT_X + wy
        return [(np.append(x, x[0]), np.append(y, y[0]))]   # close the loop

    if mode == 1:                      # x-y spiral (:2844)
        m = n // 2
        i = np.arange(m)
        rad = 0.53 + 0.43 * fR[i] + mys
        ang = fL[(i + off) % n] * 1.57 + t * 2.3
        return [(rad * np.cos(ang) * ASPECT_Y + wx,
                 rad * np.sin(ang) * ASPECT_X + wy)]

    if mode in (2, 3):                 # centered x-y spiro (:2867/:2894)
        i = np.arange(n)
        return [(fR[i] * ASPECT_Y + wx, fL[(i + off) % n] * ASPECT_X + wy)]

    if mode == 4:                      # horizontal script w/ momentum (:2922)
        m = min(n, W // 3)
        i0 = (n - m) // 2
        w1 = 0.45 + 0.5 * (mys * 0.5 + 0.5)
        w2 = 1.0 - w1
        x = -1.0 + 2.0 * (np.arange(m) / m) + wx + fR[(np.arange(m) + 6 + i0) % n] * 0.44
        y = fL[np.arange(m) + i0] * 0.47 + wy
        for i in range(2, m):          # 2nd-order momentum smoothing (:2963)
            x[i] = x[i] * w2 + w1 * (x[i - 1] * 2.0 - x[i - 2])
            y[i] = y[i] * w2 + w1 * (y[i - 1] * 2.0 - y[i - 2])
        return [(x, y)]

    if mode == 5:                      # complex-number spiro (:2984)
        i = np.arange(n)
        fLo = fL[(i + off) % n]
        x0 = fR[i] * fLo + fL[i] * fR[(i + off) % n]
        y0 = fR[i] * fR[i] - fLo * fLo
        cr, sr = math.cos(t * 0.3), math.sin(t * 0.3)
        return [((x0 * cr - y0 * sr) * ASPECT_Y + wx,
                 (x0 * sr + y0 * cr) * ASPECT_X + wy)]

    # modes 6/7 (and fallback): angle-adjustable line(s) (:3017)
    m = min(n // 2, max(16, W // 3))
    i0 = (n - m) // 2
    ang = 1.57 * mys
    dxl, dyl = math.cos(ang), math.sin(ang)
    px, py = -dxl * 1.0, -dyl * 1.0                     # line start (centered span 2)
    perp_x, perp_y = math.cos(ang + 1.57), math.sin(ang + 1.57)
    base = wx * perp_x, wx * perp_y                     # wave_x shifts along the perpendicular
    step = 2.0 / m
    idx = np.arange(m)
    along_x = base[0] + px + dxl * idx * step
    along_y = base[1] + py + dyl * idx * step
    if mode == 7:                                       # stereo pair
        sep = (wy * 0.5 + 0.5) ** 2
        lines = []
        for chan, s in ((fL, sep), (fR, -sep)):
            disp = 0.25 * chan[idx + i0] + s
            lines.append((along_x + perp_x * disp, along_y + perp_y * disp))
        return lines
    disp = 0.25 * fL[idx + i0]
    return [(along_x + perp_x * disp, along_y + perp_y * disp)]


def draw_main_wave(buf, features, preset):
    ns = preset.ns
    alpha = _f(ns, "wave_a", 0.8)
    if alpha <= 0.001:
        return
    mode = int(_f(ns, "wave_mode")) % 8
    t = features.t

    # alpha modulation by volume (:2809)
    if preset.mod_wave_alpha:
        vol = (features.bass + features.mid + features.treb) / 3.0
        denom = preset.mod_alpha_end - preset.mod_alpha_start
        alpha *= (vol - preset.mod_alpha_start) / (denom if abs(denom) > 1e-6 else 1.0)
    if mode == 1:
        alpha *= 1.25
    if mode == 3:
        alpha = 0.4 * (max(0.0, features.treb) ** 2)    # volume-tied spiro (:2899)
    alpha = float(np.clip(alpha, 0.0, 1.0))
    if alpha <= 0.001:
        return

    wave = np.clip(features.wave * preset.wave_scale, -1.0, 1.0).astype(np.float64)
    wave = _smooth(wave, preset.wave_smoothing)
    fL = wave
    fR = np.roll(wave, max(1, len(wave) // 25))         # stereo surrogate

    color = np.array([_f(ns, "wave_r", 1.0), _f(ns, "wave_g", 1.0), _f(ns, "wave_b", 1.0)])
    if _f(ns, "wave_brighten") != 0 and color.max() > 0.01:   # bMaximizeWaveColor
        color = color / color.max()
    color = color * alpha

    lines = wave_points(mode, fL, fR, t, ns, preset, features)
    pts = [_to_px(x, y) for x, y in lines]
    thick = 2 if (_f(ns, "wave_thick") != 0 or _f(ns, "wave_usedots") != 0) else 1
    additive = _f(ns, "wave_additive") != 0
    _stamp_lines(buf, pts, tuple(color), thick, additive, _f(ns, "wave_usedots") != 0)


def draw_borders(buf, ns):
    """Outer/inner border strips (:3349).  Sizes are normalized half-extents."""
    ob = _f(ns, "ob_size")
    for prefix, lo, hi in (("ob", 0.0, ob),
                           ("ib", ob, ob + _f(ns, "ib_size"))):
        a = _f(ns, f"{prefix}_a")
        if a <= 0.001 or hi <= lo:
            continue
        color = np.array([_f(ns, f"{prefix}_r"), _f(ns, f"{prefix}_g"),
                          _f(ns, f"{prefix}_b")], dtype=np.float32)
        # normalized band -> pixel thicknesses per axis (>=1 px so it shows)
        x0 = max(0, int(round(lo * W / 2)))
        x1 = max(x0 + 1, int(round(hi * W / 2)))
        y0 = max(0, int(round(lo * H / 2)))
        y1 = max(y0 + 1, int(round(hi * H / 2)))
        if x0 >= W // 2 or y0 >= H // 2:
            continue
        mask = np.zeros((H, W), dtype=bool)
        mask[y0:y1, x0:W - x0] = True
        mask[H - y1:H - y0, x0:W - x0] = True
        mask[y0:H - y0, x0:x1] = True
        mask[y0:H - y0, W - x1:W - x0] = True
        buf[mask] = buf[mask] * (1.0 - a) + color * a


_DC_MASK = None


def darken_center(buf):
    """bDarkenCenter (:3310): tiny alpha-3/32 black diamond at the center."""
    global _DC_MASK
    if _DC_MASK is None:
        # diamond radius 0.05 in normalized units -> pixels per axis
        rx = max(1.0, 0.05 * ASPECT_Y * W / 2)
        ry = max(1.0, 0.05 * H / 2)
        xs = np.arange(W) - (W - 1) / 2
        ys = (np.arange(H) - (H - 1) / 2)[:, None]
        d = np.abs(xs) / rx + np.abs(ys) / ry          # diamond distance
        _DC_MASK = (1.0 - (3.0 / 32.0) * np.clip(1.0 - d, 0.0, 1.0))[..., None].astype(np.float32)
    buf *= _DC_MASK
