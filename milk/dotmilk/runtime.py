"""MilkPreset: runs a parsed .milk file as an engine preset.

Implements MilkDrop's variable semantics (milkdropfs.cpp:483-674):
  - built-in vars reset to preset defaults every frame; user vars persist
  - per_frame_init runs once; q1..q32 are captured after init and RESTORED
    to those values at the start of every frame (per :491); per_frame's q
    values then flow into the per_pixel context (:674)
  - monitor persists from end of per_frame to the next frame (:670/:493)
  - per_pixel runs vectorized over the full pixel grid (x/y/rad/ang from
    plugin.cpp:2289) and produces array-valued steering params
"""
import numpy as np
import numpy.fft  # noqa: F401 — eager-load: spectrum() must not import post-priv-drop

from .. import config
from ..warp import ASPECT_X, ASPECT_Y, INV_ASPECT_X, INV_ASPECT_Y, NX, NY, RAD
from . import waves
from .custom import CustomShape, CustomWave
from .eel import EelError, EelNS, transpile
from .parser import parse_milk_file

W, H = config.WIDTH, config.HEIGHT

# per-pixel input grids (var_pv_x/y per milkdropfs.cpp:1756, rad/ang per plugin.cpp:2289)
PV_X = (NX * 0.5 * ASPECT_X + 0.5).astype(np.float64)        # (1,W), 0..1
PV_Y = (NY * -0.5 * ASPECT_Y + 0.5).astype(np.float64)       # (H,1)
PV_RAD = RAD.astype(np.float64)                              # (H,W)
PV_ANG = np.arctan2(NY * ASPECT_Y, NX * ASPECT_X).astype(np.float64)

NUM_Q = 32
_Q_NAMES = tuple(f"q{i}" for i in range(1, NUM_Q + 1))

# built-in per-frame var -> (INI key, default).  Reset every frame.
BUILTIN_VARS = {
    "zoom": ("zoom", 1.0), "zoomexp": ("fzoomexponent", 1.0),
    "rot": ("rot", 0.0), "warp": ("warp", 1.0),
    "cx": ("cx", 0.5), "cy": ("cy", 0.5),
    "dx": ("dx", 0.0), "dy": ("dy", 0.0),
    "sx": ("sx", 1.0), "sy": ("sy", 1.0),
    "decay": ("fdecay", 0.98),
    "wave_mode": ("nwavemode", 0.0), "wave_a": ("fwavealpha", 0.8),
    "wave_r": ("wave_r", 1.0), "wave_g": ("wave_g", 1.0), "wave_b": ("wave_b", 1.0),
    "wave_x": ("wave_x", 0.5), "wave_y": ("wave_y", 0.5),
    "wave_mystery": ("fwaveparam", 0.0),
    "wave_usedots": ("bwavedots", 0.0), "wave_thick": ("bwavethick", 0.0),
    "wave_additive": ("badditivewaves", 0.0),
    "wave_brighten": ("bmaximizewavecolor", 1.0),
    "ob_size": ("ob_size", 0.01), "ob_r": ("ob_r", 0.0), "ob_g": ("ob_g", 0.0),
    "ob_b": ("ob_b", 0.0), "ob_a": ("ob_a", 0.0),
    "ib_size": ("ib_size", 0.01), "ib_r": ("ib_r", 0.25), "ib_g": ("ib_g", 0.25),
    "ib_b": ("ib_b", 0.25), "ib_a": ("ib_a", 0.0),
    "mv_x": ("nmotionvectorsx", 12.0), "mv_y": ("nmotionvectorsy", 9.0),
    "mv_dx": ("mv_dx", 0.0), "mv_dy": ("mv_dy", 0.0), "mv_l": ("mv_l", 0.9),
    "mv_r": ("mv_r", 1.0), "mv_g": ("mv_g", 1.0), "mv_b": ("mv_b", 1.0),
    "mv_a": ("mv_a", 1.0),
    "darken_center": ("bdarkencenter", 0.0), "wrap": ("btexwrap", 1.0),
    "gamma": ("fgammaadj", 2.0),
    "echo_zoom": ("fvideoechozoom", 2.0), "echo_alpha": ("fvideoechoalpha", 0.0),
    "echo_orient": ("nvideoechoorientation", 0.0),
    "brighten": ("bbrighten", 0.0), "darken": ("bdarken", 0.0),
    "solarize": ("bsolarize", 0.0), "invert": ("binvert", 0.0),
}

# per-pixel steering outputs fed back into the warp stage
PV_STEERING = ("zoom", "zoomexp", "rot", "warp", "cx", "cy", "dx", "dy", "sx", "sy")


def _f(x, default=0.0):
    """Sanitize an EEL result to a finite python float."""
    try:
        x = float(x)
    except (TypeError, ValueError):
        x = float(np.max(x)) if np.ndim(x) else default
    return x if np.isfinite(x) else default


class MilkPreset:
    """Callable engine preset built from a .milk file.

    __call__(features) -> steering dict; .draw(buf, features) renders the
    preset's geometry (main wave, borders, darken-center) into the buffer.
    """

    def __init__(self, path):
        self.mf = parse_milk_file(path)
        self.name = self.mf.name
        sc = self.mf.scalars

        self.defaults = {var: float(sc.get(key, d)) if isinstance(sc.get(key, d), (int, float)) else d
                         for var, (key, d) in BUILTIN_VARS.items()}
        # constants (not runtime vars)
        self.warp_anim_speed = _f(sc.get("fwarpanimspeed", 1.0), 1.0)
        self.warp_scale = max(0.01, _f(sc.get("fwarpscale", 1.0), 1.0))
        self.wave_scale = _f(sc.get("fwavescale", 1.0), 1.0)
        self.wave_smoothing = min(0.98, _f(sc.get("fwavesmoothing", 0.75), 0.75))
        self.mod_wave_alpha = _f(sc.get("bmodwavealphabyvolume", 0.0), 0.0) != 0
        self.mod_alpha_start = _f(sc.get("fmodwavealphastart", 0.75), 0.75)
        self.mod_alpha_end = _f(sc.get("fmodwavealphaend", 0.95), 0.95)

        self.pf_init = transpile(self.mf.code("per_frame_init"), f"{self.name}:init")
        self.pf = transpile(self.mf.code("per_frame"), f"{self.name}:pf")
        self.pp = transpile(self.mf.code("per_pixel"), f"{self.name}:pp", scalar=False)

        self.custom_waves = [CustomWave(w, f"{self.name}:wave{i}")
                             for i, w in enumerate(self.mf.waves)
                             if w.params.get("enabled", 0) == 1]
        self.custom_shapes = [CustomShape(s, f"{self.name}:shape{i}")
                              for i, s in enumerate(self.mf.shapes)
                              if s.params.get("enabled", 0) == 1]
        self._spec_cache = (None, None)   # (frame, spectrum array)

        self.ns = EelNS()
        self.vns = EelNS()
        self.megabuf = {}
        self.q_after_init = [0.0] * NUM_Q
        self.monitor = 0.0
        self._init_done = False
        self.pp_failed = False
        self.errors = []

    # ------------------------------------------------------------------
    def _set_audio_vars(self, ns, f):
        ns["time"] = f.t
        ns["fps"] = f.fps
        ns["frame"] = float(f.frame)
        ns["progress"] = getattr(f, "progress", 0.0)
        ns["bass"] = f.bass
        ns["mid"] = f.mid
        ns["treb"] = f.treb
        ns["bass_att"] = f.bass_att
        ns["mid_att"] = f.mid_att
        ns["treb_att"] = f.treb_att
        ns["meshx"] = float(W)          # we are "mesh = every pixel"
        ns["meshy"] = float(H)
        ns["pixelsx"] = float(W)
        ns["pixelsy"] = float(H)
        ns["aspectx"] = INV_ASPECT_X    # per milkdropfs.cpp:544
        ns["aspecty"] = INV_ASPECT_Y

    def __call__(self, f):
        ns = self.ns
        ns.update(self.defaults)
        self._set_audio_vars(ns, f)

        if not self._init_done:
            self._init_done = True
            if self.pf_init:
                try:
                    self.pf_init.run(ns, self.megabuf)
                except Exception as e:
                    self.errors.append(f"per_frame_init: {e}")
            self.q_after_init = [_f(ns[q]) for q in _Q_NAMES]
            self.monitor = _f(ns["monitor"])

        for q, val in zip(_Q_NAMES, self.q_after_init):   # :491
            ns[q] = val
        ns["monitor"] = self.monitor                       # :493

        if self.pf:
            try:
                self.pf.run(ns, self.megabuf)
            except Exception as e:
                self.errors.append(f"per_frame: {e}")
        self.monitor = _f(ns["monitor"])                   # :670

        p = {
            "decay": min(1.0, max(0.0, _f(ns["decay"], 0.98))),
            "border": "wrap" if _f(ns["wrap"]) != 0 else "constant",
            "zoom": _f(ns["zoom"], 1.0), "zoom_exp": _f(ns["zoomexp"], 1.0),
            "rot": _f(ns["rot"]), "warp": _f(ns["warp"]),
            "warp_anim_speed": self.warp_anim_speed, "warp_scale": self.warp_scale,
            "cx": _f(ns["cx"], 0.5), "cy": _f(ns["cy"], 0.5),
            "dx": _f(ns["dx"]), "dy": _f(ns["dy"]),
            "sx": _f(ns["sx"], 1.0), "sy": _f(ns["sy"], 1.0),
            "brightness": max(0.0, _f(ns["gamma"], 2.0)) * 0.5,
            # MilkDrop's comp stage multiplies by fGammaAdj (typ. 2.0) into a
            # saturating 8-bit target; 0.5x rescales so gamma=2.0 ≡ neutral here.
        }

        if self.pp and not self.pp_failed:
            arrays = self._run_per_pixel(f, p)
            if arrays:
                p.update(arrays)

        return p

    def _run_per_pixel(self, f, p):
        vns = self.vns
        self._set_audio_vars(vns, f)
        for q, name in zip(_Q_NAMES, _Q_NAMES):
            vns[name] = _f(self.ns[name])
        # per-vertex inputs (:1756-1769): steering starts at post-per-frame values
        vns["x"] = PV_X
        vns["y"] = PV_Y
        vns["rad"] = PV_RAD
        vns["ang"] = PV_ANG
        for k in PV_STEERING:
            vns[k] = p["zoom_exp"] if k == "zoomexp" else p[k]
        try:
            self.pp.run(vns, self.megabuf)
        except Exception as e:
            self.pp_failed = True
            self.errors.append(f"per_pixel: {e}")
            return None
        out = {}
        for k in PV_STEERING:
            val = vns[k]
            key = "zoom_exp" if k == "zoomexp" else k
            if np.ndim(val) != 0:
                out[key] = np.nan_to_num(np.asarray(val, dtype=np.float32),
                                         nan=0.0, posinf=1e3, neginf=-1e3)
            else:
                out[key] = _f(val, p[key])
        return out

    # ------------------------------------------------------------------
    def spectrum(self, f):
        """Synthesized spectrum for bSpectrum custom waves (the packet carries
        only the waveform; a 128-pt rfft is plenty at this resolution)."""
        if self._spec_cache[0] != f.frame:
            mag = np.abs(np.fft.rfft(f.wave))            # 65 bins
            self._spec_cache = (f.frame, mag.astype(np.float32))
        return self._spec_cache[1]

    def draw(self, buf, f):
        """Render this preset's geometry into the feedback buffer.
        Shapes first, then custom waves, then the main wave (:1054)."""
        for shape in self.custom_shapes:
            try:
                shape.draw(buf, f, self)
            except Exception as e:
                self.errors.append(f"shape: {e}")
        for cwave in self.custom_waves:
            try:
                cwave.draw(buf, f, self)
            except Exception as e:
                self.errors.append(f"cwave: {e}")
        try:
            waves.draw_main_wave(buf, f, self)
        except Exception as e:
            self.errors.append(f"wave draw: {e}")
        waves.draw_borders(buf, self.ns)
        if _f(self.ns["darken_center"]) != 0:
            waves.darken_center(buf)
