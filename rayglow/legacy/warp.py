"""Per-pixel UV displacement maps — a faithful vectorized port of MilkDrop's
per-vertex warp math (MilkDrop3/code/vis_milk2/milkdropfs.cpp:1698-1837).

MilkDrop ran this on a coarse mesh because 2001 GPUs couldn't afford per-pixel
math; at 256x32 = 8k px we just do every pixel.  cv2.remap supplies the
bilinear sampling the GPU used to.

Coordinate conventions (matching the original):
  - Grid x,y in [-1,1], D3D-style: y=+1 at the TOP of the screen.
  - Aspect scalars make the shorter axis span less, so all the math happens in
    an isotropic space where rotation is circular in *physical pixels*; the
    final inverse-aspect step expands back to texture coords.
    For our wide panel: aspect_x=1, aspect_y=H/W.
  - rad is the aspect-corrected radius from center (0 at center, ~1 at the
    short edges of the long axis).
"""
import math

import numpy as np

from ..feed import config

W, H = config.WIDTH, config.HEIGHT

# m_fAspectX/Y semantics (plugin.cpp:2035): shorter axis gets the <1 factor.
ASPECT_X = W / H if H > W else 1.0
ASPECT_Y = H / W if W > H else 1.0
INV_ASPECT_X = 1.0 / ASPECT_X
INV_ASPECT_Y = 1.0 / ASPECT_Y

# Normalized grids, computed once.  Kept as (1,W) / (H,1) for cheap broadcasting.
NX = (np.arange(W, dtype=np.float32) / (W - 1) * 2.0 - 1.0)[None, :]
NY = -(np.arange(H, dtype=np.float32) / (H - 1) * 2.0 - 1.0)[:, None]  # +1 = top

# rad per plugin.cpp:2289: sqrt(x²·aspx² + y²·aspy²)
RAD = np.sqrt((NX * ASPECT_X) ** 2 + (NY * ASPECT_Y) ** 2).astype(np.float32)

# Parameters that feed the map computation (used for cache keying).
MAP_PARAMS = ("zoom", "zoom_exp", "rot", "warp", "warp_anim_speed", "warp_scale",
              "cx", "cy", "dx", "dy", "sx", "sy")


def _live(x, neutral):
    """True if param is an array (per-pixel steering) or a non-neutral scalar."""
    return np.ndim(x) != 0 or x != neutral


def compute_maps(p):
    """Steering params -> (map_x, map_y) float32 pixel-coordinate maps for cv2.remap.

    Transform order (milkdropfs.cpp:1794-1833): zoom(rad) -> stretch ->
    drunken-sine warp -> rotate about (cx,cy) -> translate -> inverse-aspect.

    Every steering param may be a scalar OR an (H,W)/broadcastable array
    (.milk per_pixel equations produce arrays) — true per-pixel, no mesh.
    """
    zoom, zoom_exp = p["zoom"], p["zoom_exp"]
    rot, warp = p["rot"], p["warp"]
    cx, cy = p["cx"], p["cy"]
    dx, dy = p["dx"], p["dy"]
    sx, sy = p["sx"], p["sy"]

    # zoom, radius-shaped by zoom_exp (:1794)
    with np.errstate(all="ignore"):
        zoom2_inv = np.nan_to_num(zoom ** -(zoom_exp ** (RAD * 2.0 - 1.0)),
                                  nan=1.0, posinf=1.0, neginf=1.0)

    # initial texcoords with built-in zoom (:1798)
    u = NX * (ASPECT_X * 0.5) * zoom2_inv + 0.5
    v = -NY * (ASPECT_Y * 0.5) * zoom2_inv + 0.5

    # stretch about (cx,cy) (:1806)
    if _live(sx, 1.0):
        u = (u - cx) / np.where(sx == 0, 1.0, sx) + cx
    if _live(sy, 1.0):
        v = (v - cy) / np.where(sy == 0, 1.0, sy) + cy

    # drunken-sine warp (:1702, :1812) — four incommensurate cosines so the
    # wobble never visibly repeats.  Note: spatial terms use the RAW grid
    # coords (m_verts[n].x/y), not the aspect-corrected ones.
    if _live(warp, 0.0):
        wt = p["time"] * p["warp_anim_speed"]
        wsi = 1.0 / p["warp_scale"]
        f0 = 11.68 + 4.0 * math.cos(wt * 1.413 + 10)
        f1 = 8.77 + 3.0 * math.cos(wt * 1.113 + 7)
        f2 = 10.54 + 3.0 * math.cos(wt * 1.233 + 3)
        f3 = 11.49 + 4.0 * math.cos(wt * 0.933 + 5)
        amp = warp * 0.0035
        u = u + amp * np.sin(wt * 0.333 + wsi * (NX * f0 - NY * f3))
        v = v + amp * np.cos(wt * 0.375 - wsi * (NX * f2 + NY * f1))
        u = u + amp * np.cos(wt * 0.753 - wsi * (NX * f1 - NY * f2))
        v = v + amp * np.sin(wt * 0.825 + wsi * (NX * f0 + NY * f3))

    # rotation about (cx,cy) (:1819)
    if _live(rot, 0.0):
        u2 = u - cx
        v2 = v - cy
        cr, sr = np.cos(rot), np.sin(rot)
        u = u2 * cr - v2 * sr + cx
        v = u2 * sr + v2 * cr + cy

    # translation (:1828)
    u = u - dx
    v = v - dy

    # undo aspect ratio fix (:1832)
    u = (u - 0.5) * INV_ASPECT_X + 0.5
    v = (v - 0.5) * INV_ASPECT_Y + 0.5

    # normalized [0,1] -> pixel coordinates for cv2.remap
    map_x = (u * (W - 1)).astype(np.float32, copy=False)
    map_y = (v * (H - 1)).astype(np.float32, copy=False)
    # broadcasting can leave a (1,W)/(H,1) shape if zoom math collapsed; ensure full
    map_x = np.broadcast_to(map_x, (H, W)).astype(np.float32, copy=False)
    map_y = np.broadcast_to(map_y, (H, W)).astype(np.float32, copy=False)
    return np.ascontiguousarray(map_x), np.ascontiguousarray(map_y)


class WarpCache:
    """Reuse maps while steering params are unchanged.

    warp != 0 makes the maps time-dependent, so those recompute every frame
    (still ~sub-ms at 8k px); everything else recomputes only on change.
    """

    def __init__(self):
        self._key = None
        self._maps = None

    def maps(self, p):
        vals = [p[k] for k in MAP_PARAMS]
        if any(np.ndim(x) != 0 for x in vals) or p["warp"] != 0.0:
            return compute_maps(p)      # array steering / animated warp: no cache
        key = tuple(vals)
        if key != self._key:
            self._maps = compute_maps(p)
            self._key = key
        return self._maps
