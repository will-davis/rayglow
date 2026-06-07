"""Engine: wires receiver -> features -> preset -> warp -> buffer -> frame.

Frame order matches MilkDrop's RenderFrame (milkdropfs.cpp:751):
per-frame equations (preset) -> warp remap of previous frame -> decay ->
draw audio geometry -> composite (gamma, outside the feedback loop).
"""
import time

from . import draw
from ..feed import config
from ..feed.features import FeatureState
from .framebuffer import FeedbackBuffer
from .milkpresets import DEFAULTS, PRESETS
from ..feed.receiver import Receiver
from .warp import WarpCache


class Engine:
    def __init__(self, preset_name="tunnel", listen=True, profile=False):
        self.fb = FeedbackBuffer()
        self.features = FeatureState()
        self.receiver = Receiver() if listen else None
        self.preset = None
        self.set_preset(preset_name)
        self.cache = WarpCache()
        self.profile = profile
        self.stage_ms = {"maps": 0.0, "warp": 0.0, "draw": 0.0, "composite": 0.0}

    def set_preset(self, preset):
        """preset: a registry name, a .milk path, or a preset object.
        The feedback buffer is intentionally kept — MilkDrop hard-cuts
        carry the previous frame into the new preset's warp."""
        if isinstance(preset, str):
            if preset.endswith(".milk"):
                from .dotmilk.runtime import MilkPreset
                preset = MilkPreset(preset)
            else:
                preset = PRESETS[preset]
        self.preset = preset
        self.cache = WarpCache()

    def step(self, now, dt):
        """Run one frame; returns the uint8 (H,W,3) frame for the matrix."""
        pkt = self.receiver.poll() if self.receiver else None
        self.features.update(pkt, now, dt)

        p = dict(DEFAULTS)
        p.update(self.preset(self.features))
        p["time"] = now

        if self.profile:
            return self._step_profiled(p)

        map_x, map_y = self.cache.maps(p)
        self.fb.warp(map_x, map_y, p["border"])
        self.fb.decay(p["decay"])
        self._draw(p)
        self.fb.clamp()
        return self.fb.composite(brightness=p.get("brightness", 1.0))

    def _draw(self, p):
        """Geometry injection: .milk presets bring their own draw();
        python presets use the default waveform polyline."""
        drawer = getattr(self.preset, "draw", None)
        if drawer is not None:
            drawer(self.fb.buf, self.features)
        else:
            draw.waveform(self.fb.buf, self.features.wave, p["wave_color"],
                          thickness=p["wave_thickness"], amp=p["wave_amp"],
                          y_center=p["wave_y"])

    def _step_profiled(self, p):
        t0 = time.perf_counter()
        map_x, map_y = self.cache.maps(p)
        t1 = time.perf_counter()
        self.fb.warp(map_x, map_y, p["border"])
        self.fb.decay(p["decay"])
        t2 = time.perf_counter()
        self._draw(p)
        self.fb.clamp()
        t3 = time.perf_counter()
        frame = self.fb.composite(brightness=p.get("brightness", 1.0))
        t4 = time.perf_counter()
        a = 0.05  # EMA so the printout is stable
        for key, ms in (("maps", t1 - t0), ("warp", t2 - t1),
                        ("draw", t3 - t2), ("composite", t4 - t3)):
            self.stage_ms[key] += a * (ms * 1000.0 - self.stage_ms[key])
        return frame

    def warmup(self):
        """Exercise every code path BEFORE RGBMatrix() drops root privileges.

        After the drop, /home/will is unreadable: file loads AND lazy module
        imports (numpy submodules, cv2 internals) fail.  Run real frames with
        synth features so cv2.remap, cv2.polylines, and composite all resolve
        their imports now, then reset the buffer.
        """
        for i in range(3):
            self.features._last_pkt_time = None      # force synth fallback path
            self.step(now=0.05 * (i + 1), dt=0.05)
        self.fb.reset()
        self.features = FeatureState()
        self.cache = WarpCache()
