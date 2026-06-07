"""Custom waves + custom shapes — port of DrawCustomWaves/DrawCustomShapes
(milkdropfs.cpp:2496/2215).  These are where MilkDrop2-era presets draw most
of their geometry (the main wave is often disabled entirely).

Coordinate notes (ported exactly):
  - wave per-point x,y are 0..1 in an ISOTROPIC width-unit space:
    x_d3d=(x*2-1)*invAspectX, y_d3d=(y*-2+1)*invAspectY (:2612).  On our 8:1
    panel invAspectY=8, so only y in [0.4375,0.5625] is on-screen — consistent
    with the per-pixel y variable's range.
  - shape x,y span the full screen (no invAspect, :2299); shape rad gets
    aspectY on x only (:2318) so shapes are circular in physical pixels.
  - value scale: MilkDrop's fWaveform is ±128-ish with mult=0.004 (:2555);
    our packet wave is ±1.0, so the equivalent multiplier is 0.512.

Execution strategies:
  - wave per_point runs VECTORIZED over all samples; waves that accumulate
    user vars across points (ma=ma+...) collapse under vectorization — those
    are auto-detected (x AND y come out scalar) and re-run SEQUENTIALLY with
    the sample count capped (true EEL semantics, bounded cost).
  - shape per_frame runs once per instance; multi-instance shapes try a
    VECTORIZED pass over instance=arange(n) first, falling back to a capped
    scalar loop if the code won't vectorize.
"""
import math

import cv2
import numpy as np

from .. import config
from ..warp import ASPECT_Y, INV_ASPECT_X, INV_ASPECT_Y
from .eel import EelNS, transpile

W, H = config.WIDTH, config.HEIGHT

NUM_Q = 32
NUM_T = 8
_Q = tuple(f"q{i}" for i in range(1, NUM_Q + 1))
_T = tuple(f"t{i}" for i in range(1, NUM_T + 1))

WAVE_MULT = 0.512        # ±1 wave -> the value range MilkDrop's 0.004*±128 gave
SPEC_MULT = 0.15         # spectrum multiplier (:2555)
SEQ_SAMPLE_CAP = 128     # sample cap for sequential per-point mode (cost bound)
SCALAR_INST_CAP = 64     # instance cap when vectorized shape pf falls back


def _join(pairs):
    return "\n".join(code for _n, code in sorted(pairs))


def _f(x, default=0.0):
    try:
        x = float(x)
        return x if math.isfinite(x) else default
    except (TypeError, ValueError):
        return default


def _seed_context(ns, parent_ns, f):
    """Read-only vars every custom-wave/shape context sees (:2530)."""
    for k in ("time", "fps", "frame", "progress", "bass", "mid", "treb",
              "bass_att", "mid_att", "treb_att"):
        ns[k] = parent_ns[k]
    for q in _Q:
        ns[q] = _f(parent_ns[q])


def _ns():
    return EelNS()


# ----------------------------------------------------------------------------
# custom waves
# ----------------------------------------------------------------------------

class CustomWave:
    def __init__(self, mw, name):
        self.params = mw.params
        self.enabled = mw.params.get("enabled", 0) == 1
        self.init = transpile(_join(mw.init_code), f"{name}:init")
        self.pf = transpile(_join(mw.per_frame), f"{name}:pf")
        self.pp = transpile(_join(mw.per_point), f"{name}:pp", scalar=False)
        self.pp_seq = transpile(_join(mw.per_point), f"{name}:pp_seq", scalar=True)
        self.ns = None           # persistent per-frame context
        self.vns = None          # persistent per-point context
        self.megabuf = {}
        self.t_after_init = [0.0] * NUM_T
        self._init_done = False
        self.seq_mode = None     # None=auto-detect, False=vectorized, True=sequential
        self.errors = []

    def draw(self, buf, f, preset):
        if self.ns is None:
            self.ns = _ns()
        ns = self.ns
        p = self.params
        _seed_context(ns, preset.ns, f)
        for k, d in (("r", 1.0), ("g", 1.0), ("b", 1.0), ("a", 1.0),
                     ("samples", 512.0), ("scaling", 1.0), ("smoothing", 0.5),
                     ("sep", 0.0)):
            ns[k] = _f(p.get(k, d), d)

        if not self._init_done:
            self._init_done = True
            if self.init:
                try:
                    self.init.run(ns, self.megabuf)
                except Exception as e:
                    self.errors.append(f"init: {e}")
            self.t_after_init = [_f(ns[t]) for t in _T]
        for t, val in zip(_T, self.t_after_init):
            ns[t] = val

        if self.pf:
            try:
                self.pf.run(ns, self.megabuf)
            except Exception as e:
                self.errors.append(f"pf: {e}")
                return

        n = int(np.clip(_f(ns["samples"], 512), 2, 512))
        sep = int(np.clip(_f(ns["sep"]), 0, n - 1))
        n -= sep
        if self.seq_mode:
            n = min(n, SEQ_SAMPLE_CAP)
        if n < 2:
            return
        d1, d2 = self._sample_data(f, preset, ns, n, sep)

        if self.pp and self.seq_mode is None:
            res = self._run_pp_vectorized(f, preset, ns, n, d1, d2)
            if res is None:
                return
            x = res["x"]
            # degenerate: nothing varies per-point -> code accumulates across
            # points; vectorization collapsed it.  Switch to sequential.
            if np.ndim(res["x"]) == 0 and np.ndim(res["y"]) == 0:
                self.seq_mode = True
                n = min(n, SEQ_SAMPLE_CAP)
                d1, d2 = d1[:n], d2[:n]
            else:
                self.seq_mode = False
                self._render(buf, p, n, res)
                return
        if self.pp and self.seq_mode:
            res = self._run_pp_sequential(f, preset, ns, n, d1, d2)
        elif self.pp:
            res = self._run_pp_vectorized(f, preset, ns, n, d1, d2)
        else:
            res = {"x": 0.5 + d1, "y": 0.5 + d2,
                   "r": _f(ns["r"], 1), "g": _f(ns["g"], 1),
                   "b": _f(ns["b"], 1), "a": _f(ns["a"], 1)}
        if res is not None:
            self._render(buf, p, n, res)

    def _sample_data(self, f, preset, ns, n, sep):
        spectrum = self.params.get("bspectrum", 0) == 1
        src = preset.spectrum(f) if spectrum else f.wave
        mult = (SPEC_MULT if spectrum else WAVE_MULT) * _f(ns["scaling"], 1.0) \
            * preset.wave_scale
        idx = np.minimum((np.arange(n) * (len(src) / n)).astype(int), len(src) - 1)
        d1 = src[idx].astype(np.float64)
        d2 = src[np.minimum(idx + sep, len(src) - 1)].astype(np.float64)
        mix1 = math.sqrt(max(0.0, _f(ns["smoothing"], 0.5)) * 0.98)   # (:2563)
        if mix1 > 0.0:
            mix2 = 1.0 - mix1
            for j in range(1, n):
                d1[j] = d1[j] * mix2 + d1[j - 1] * mix1
                d2[j] = d2[j] * mix2 + d2[j - 1] * mix1
            for j in range(n - 2, -1, -1):
                d1[j] = d1[j] * mix2 + d1[j + 1] * mix1
                d2[j] = d2[j] * mix2 + d2[j + 1] * mix1
        return d1 * mult, d2 * mult

    def _seed_pp(self, vns, preset, f, ns):
        _seed_context(vns, preset.ns, f)
        for t in _T:                       # t's flow pf -> pp (:2545)
            vns[t] = _f(ns[t])

    def _run_pp_vectorized(self, f, preset, ns, n, d1, d2):
        if self.vns is None:
            self.vns = _ns()
        vns = self.vns
        self._seed_pp(vns, preset, f, ns)
        vns["sample"] = np.arange(n) / (n - 1)
        vns["value1"] = d1
        vns["value2"] = d2
        vns["x"] = 0.5 + d1
        vns["y"] = 0.5 + d2
        for c in ("r", "g", "b", "a"):
            vns[c] = _f(ns[c], 1.0)
        try:
            self.pp.run(vns, self.megabuf)
        except Exception:
            self.seq_mode = True           # vector run failed -> try sequential
            return self._run_pp_sequential(f, preset, ns, min(n, SEQ_SAMPLE_CAP),
                                           d1[:SEQ_SAMPLE_CAP], d2[:SEQ_SAMPLE_CAP])
        return {k: vns[k] for k in ("x", "y", "r", "g", "b", "a")}

    def _run_pp_sequential(self, f, preset, ns, n, d1, d2):
        """True EEL semantics: user vars accumulate across points."""
        if self.vns is None:
            self.vns = _ns()
        vns = self.vns
        self._seed_pp(vns, preset, f, ns)
        out = {k: np.empty(n) for k in ("x", "y", "r", "g", "b", "a")}
        base = {c: _f(ns[c], 1.0) for c in ("r", "g", "b", "a")}
        inv = 1.0 / (n - 1)
        try:
            for j in range(n):
                vns["sample"] = j * inv
                vns["value1"] = d1[j]
                vns["value2"] = d2[j]
                vns["x"] = 0.5 + d1[j]
                vns["y"] = 0.5 + d2[j]
                for c, v in base.items():
                    vns[c] = v
                self.pp_seq.run(vns, self.megabuf)
                for k in out:
                    out[k][j] = _f(vns[k], 0.5)
        except Exception as e:
            self.errors.append(f"pp(seq): {e}")
            return None
        return out

    def _render(self, buf, p, n, res):
        def arr(key, default):
            val = res.get(key, default)
            return np.broadcast_to(
                np.nan_to_num(np.asarray(val, dtype=np.float64)), (n,))
        x = arr("x", 0.5)
        y = arr("y", 0.5)
        # 0..1 isotropic -> D3D -> pixels (:2612).  WAVE_FIT blends the faithful
        # inverse-aspect (content cropped to a horizontal slice at 8:1) toward a
        # squashed-to-fit mapping where the whole designed canvas is visible.
        fit = config.WAVE_FIT
        inv_y = INV_ASPECT_Y * (1.0 - fit) + 1.0 * fit
        xd = (x * 2.0 - 1.0) * INV_ASPECT_X
        yd = (y * -2.0 + 1.0) * inv_y
        px = np.clip((xd + 1.0) * 0.5 * (W - 1), -4 * W, 5 * W)
        py = np.clip((1.0 - yd) * 0.5 * (H - 1), -4 * H, 5 * H)
        rgb = np.stack([arr("r", 1.0), arr("g", 1.0), arr("b", 1.0)], axis=1)
        _blend_polyline(buf, px, py, rgb, arr("a", 1.0),
                        p.get("badditive", 0) == 1,
                        p.get("bdrawthick", 0) == 1,
                        p.get("busedots", 0) == 1)


def _blend_polyline(buf, px, py, rgb, alpha, additive, thick, dots):
    """Polyline with per-point color/alpha; per-segment lines when colors vary."""
    n = len(px)
    if n < 2 and not dots:
        return
    pts = np.stack([px, py], axis=1).astype(np.int32)
    a = np.clip(alpha, 0.0, 1.0)
    col = np.clip(rgb, 0.0, 1.0) * a[:, None]      # premultiplied
    if float(col.max()) <= 0.003:
        return
    stamp = np.zeros_like(buf)
    thickness = 2 if thick else 1
    if dots:
        for k in range(n):
            c = col[k]
            if c.max() > 0.003:
                cv2.circle(stamp, (int(pts[k, 0]), int(pts[k, 1])), 0,
                           (float(c[0]), float(c[1]), float(c[2])), -1)
    else:
        var = float(np.abs(np.diff(col, axis=0)).max()) if n > 1 else 0.0
        if var < 0.02:                              # constant color: one call
            c = col[n // 2]
            cv2.polylines(stamp, [pts.reshape(-1, 1, 2)], False,
                          (float(c[0]), float(c[1]), float(c[2])),
                          thickness, cv2.LINE_AA)
        else:
            for k in range(n - 1):
                c = (col[k] + col[k + 1]) * 0.5
                if c.max() > 0.003:
                    cv2.line(stamp, tuple(pts[k]), tuple(pts[k + 1]),
                             (float(c[0]), float(c[1]), float(c[2])),
                             thickness, cv2.LINE_AA)
    if additive:
        buf += stamp
    else:
        # approximate src-alpha blend: where the stamp drew, fade base by mean alpha
        mask = stamp.max(axis=2, keepdims=True) > 0.003
        am = float(a.mean())
        np.copyto(buf, buf * (1.0 - am) + stamp, where=mask)


# ----------------------------------------------------------------------------
# custom shapes
# ----------------------------------------------------------------------------

_SHAPE_DEFAULTS = (("sides", 4.0), ("additive", 0.0), ("thickoutline", 0.0),
                   ("textured", 0.0), ("x", 0.5), ("y", 0.5), ("rad", 0.1),
                   ("ang", 0.0), ("tex_ang", 0.0), ("tex_zoom", 1.0),
                   ("r", 1.0), ("g", 0.0), ("b", 0.0), ("a", 1.0),
                   ("r2", 0.0), ("g2", 1.0), ("b2", 0.0), ("a2", 0.0),
                   ("border_r", 1.0), ("border_g", 1.0), ("border_b", 1.0),
                   ("border_a", 0.1), ("num_inst", 1.0))
_SHAPE_OUT = ("sides", "additive", "thick", "textured", "x", "y", "rad", "ang",
              "tex_ang", "tex_zoom", "r", "g", "b", "a", "r2", "g2", "b2", "a2",
              "border_r", "border_g", "border_b", "border_a")


class CustomShape:
    def __init__(self, ms, name):
        self.params = ms.params
        self.enabled = ms.params.get("enabled", 0) == 1
        self.init = transpile(_join(ms.init_code), f"{name}:init")
        self.pf = transpile(_join(ms.per_frame), f"{name}:pf")
        self.pf_vec = None
        try:                              # vectorized variant for many instances
            self.pf_vec = transpile(_join(ms.per_frame), f"{name}:pf_vec", scalar=False)
        except Exception:
            pass
        self.ns = None
        self.megabuf = {}
        self.t_after_init = [0.0] * NUM_T
        self._init_done = False
        self.vec_ok = None                # None=untried
        self.errors = []

    def _set_defaults(self, ns, n_inst):
        for k, d in _SHAPE_DEFAULTS:
            ns["thick" if k == "thickoutline" else k] = _f(self.params.get(k, d), d)
        return n_inst

    def draw(self, buf, f, preset):
        if self.ns is None:
            self.ns = _ns()
        ns = self.ns
        n_inst = int(np.clip(_f(self.params.get("num_inst", 1), 1), 1, 1024))

        if not self._init_done:
            self._init_done = True
            _seed_context(ns, preset.ns, f)
            self._set_defaults(ns, n_inst)
            ns["instance"] = 0.0
            if self.init:
                try:
                    self.init.run(ns, self.megabuf)
                except Exception as e:
                    self.errors.append(f"init: {e}")
            self.t_after_init = [_f(ns[t]) for t in _T]

        # vectorized path: one EEL run over instance=arange(n)
        if n_inst > 1 and self.pf_vec is not None and self.vec_ok is not False:
            if self._draw_vectorized(buf, f, preset, n_inst):
                return
        # scalar path
        for instance in range(min(n_inst, SCALAR_INST_CAP)):
            _seed_context(ns, preset.ns, f)
            self._set_defaults(ns, n_inst)
            ns["instance"] = float(instance)
            for t, val in zip(_T, self.t_after_init):
                ns[t] = val
            if self.pf:
                try:
                    self.pf.run(ns, self.megabuf)
                except Exception as e:
                    self.errors.append(f"pf: {e}")
                    return
            vals = {k: _f(ns[k], dict(_SHAPE_DEFAULTS).get(k, 0.0)) for k in _SHAPE_OUT}
            _draw_shape(buf, vals, preset)

    def _draw_vectorized(self, buf, f, preset, n_inst):
        ns = self.ns
        _seed_context(ns, preset.ns, f)
        self._set_defaults(ns, n_inst)
        ns["instance"] = np.arange(n_inst, dtype=np.float64)
        for t, val in zip(_T, self.t_after_init):
            ns[t] = val
        try:
            self.pf_vec.run(ns, self.megabuf)
        except Exception:
            self.vec_ok = False
            return False
        self.vec_ok = True
        arrs = {}
        for k in _SHAPE_OUT:
            val = ns[k]
            arrs[k] = np.broadcast_to(np.nan_to_num(
                np.asarray(val, dtype=np.float64)), (n_inst,))
        for i in range(n_inst):
            _draw_shape(buf, {k: float(arrs[k][i]) for k in _SHAPE_OUT}, preset)
        return True


def _c01(x):
    return 0.0 if x < 0.0 else (1.0 if x > 1.0 else x)


def _draw_shape(buf, v, preset):
    """One shape instance, drawn ROI-bounded.  Presets spawn hundreds of
    instances per frame, so this path is scalar-math + early-out heavy."""
    a1 = _c01(v["a"])
    a2 = _c01(v["a2"])
    ba = _c01(v["border_a"])
    if a1 <= 0.004 and a2 <= 0.004 and ba <= 0.004:
        return
    rad = v["rad"]
    if rad <= 0.0001:
        return
    cxd = v["x"] * 2.0 - 1.0                        # (:2299)
    cyd = v["y"] * -2.0 + 1.0
    cpx = (cxd + 1.0) * 0.5 * (W - 1)
    cpy = (1.0 - cyd) * 0.5 * (H - 1)
    rpx = rad * ASPECT_Y * 0.5 * (W - 1)            # physical-circular (:2318)
    rpy = rad * 0.5 * (H - 1)
    if cpx + rpx < 0 or cpx - rpx >= W or cpy + rpy < 0 or cpy - rpy >= H:
        return                                       # fully offscreen

    # tiny shapes (most multi-instance swarms): single-pixel splat
    if rpx <= 0.75 and rpy <= 0.75 and v["textured"] == 0:
        ix, iy = int(cpx + 0.5), int(cpy + 0.5)
        if 0 <= ix < W and 0 <= iy < H:
            am = max(a1, ba)
            r = _c01(v["r"]) * a1 + _c01(v["border_r"]) * ba
            g = _c01(v["g"]) * a1 + _c01(v["border_g"]) * ba
            b = _c01(v["b"]) * a1 + _c01(v["border_b"]) * ba
            if v["additive"] != 0:
                buf[iy, ix, 0] += r
                buf[iy, ix, 1] += g
                buf[iy, ix, 2] += b
            else:
                buf[iy, ix] = buf[iy, ix] * (1.0 - am) + (r, g, b)
        return

    sides = 3 if v["sides"] < 3 else (100 if v["sides"] > 100 else int(v["sides"]))
    th = _TH_BASE[sides] + (v["ang"] + math.pi * 0.25)
    px = cpx + rpx * np.cos(th)
    py = cpy + rpy * np.sin(th)

    x0 = int(max(0.0, px.min() - 1.0))
    x1 = int(min(float(W), px.max() + 2.0))
    y0 = int(max(0.0, py.min() - 1.0))
    y1 = int(min(float(H), py.max() + 2.0))
    if x0 >= x1 or y0 >= y1:
        return
    roi = buf[y0:y1, x0:x1]
    pts = np.empty((sides, 1, 2), dtype=np.int32)
    pts[:, 0, 0] = (px - x0).astype(np.int32)
    pts[:, 0, 1] = (py - y0).astype(np.int32)
    additive = v["additive"] != 0

    if v["textured"] != 0:
        if max(a1, a2) > 0.004:
            _draw_textured_fill(buf, roi, pts, v, (x0, y0), a1, a2, additive)
    elif max(a1, a2) > 0.004:
        stamp = np.zeros_like(roi)
        amask = np.zeros(roi.shape[:2] + (1,), dtype=np.float32)
        ccx, ccy = cpx - x0, cpy - y0
        rings = 3 if max(rpx, rpy) > 4.0 else 2     # gradient detail by size
        ring = np.empty_like(pts)
        for k in range(rings, 0, -1):               # outer -> inner
            frac = k / rings
            ring[:, 0, 0] = (ccx + (pts[:, 0, 0] - ccx) * frac).astype(np.int32)
            ring[:, 0, 1] = (ccy + (pts[:, 0, 1] - ccy) * frac).astype(np.int32)
            aa = a1 * (1.0 - frac) + a2 * frac
            f1, f2 = (1.0 - frac) * aa, frac * aa
            cv2.fillPoly(stamp, [ring],
                         (v["r"] * f1 + v["r2"] * f2,
                          v["g"] * f1 + v["g2"] * f2,
                          v["b"] * f1 + v["b2"] * f2))
            cv2.fillPoly(amask, [ring], aa)
        if additive:
            roi += stamp
        else:
            roi *= (1.0 - np.clip(amask, 0.0, 1.0))
            roi += stamp

    if ba > 0.004:
        stamp = np.zeros_like(roi)
        cv2.polylines(stamp, [pts], True,
                      (_c01(v["border_r"]) * ba, _c01(v["border_g"]) * ba,
                       _c01(v["border_b"]) * ba),
                      2 if v["thick"] != 0 else 1, cv2.LINE_AA)
        roi += stamp                                 # borders additively (close enough)


# unit-circle angle tables per side count, built once
_TH_BASE = {s: np.arange(s) / s * 2.0 * math.pi for s in range(3, 101)}


def _draw_textured_fill(buf, roi, pts, v, origin, a1, a2, additive):
    """Textured shape: polygon filled with an affine resample of the previous
    frame — scale 1/(tex_zoom*rad), rotate (tex_ang-ang), about the shape
    center.  This is MilkDrop's inner-zoom/feedback-shape effect (:2321)."""
    x0, y0 = origin
    bh, bw = roi.shape[:2]
    scale = 1.0 / max(0.01, v["tex_zoom"] * v["rad"])
    delta = v["tex_ang"] - v["ang"]
    cd, sd = math.cos(delta), math.sin(delta)
    A = scale * np.array([[cd, sd], [-sd, cd]])
    c_scr = np.array([(v["x"] * 2 - 1 + 1) * 0.5 * (W - 1),
                      (1 - (v["y"] * -2 + 1)) * 0.5 * (H - 1)])
    c_tex = np.array([0.5 * (W - 1), 0.5 * (H - 1)])
    # src = A @ (dst_local + [x0,y0] - c_scr) + c_tex   (dst -> src, ROI-local)
    b = c_tex - A @ (c_scr - np.array([x0, y0]))
    M = np.hstack([A, b[:, None]]).astype(np.float32)
    warped = cv2.warpAffine(buf, M, (bw, bh),
                            flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                            borderMode=cv2.BORDER_WRAP)
    # tint by shape color, alpha gradient via 2 rings (center a1 -> edge a2)
    tint = np.clip(np.array([v["r"], v["g"], v["b"]], dtype=np.float32), 0, 2)
    if not np.allclose(tint, 1.0):
        warped = warped * tint
    amask = np.zeros((bh, bw, 1), dtype=np.float32)
    center = pts.reshape(-1, 2).mean(axis=0)
    for k, frac in ((0, 1.0), (1, 0.55)):
        ring = (center + (pts.reshape(-1, 2) - center) * frac
                ).astype(np.int32).reshape(-1, 1, 2)
        aa = a1 * (1 - frac) + a2 * frac
        cv2.fillPoly(amask, [ring], float(max(aa, 0.0)))
    np.clip(amask, 0.0, 1.0, out=amask)
    if additive:
        roi += warped * amask
    else:
        roi *= (1.0 - amask)
        roi += warped * amask
