"""Parser for .milk preset files (MilkDrop 1.x/2.x/2077 INI-ish format).

Layout: optional header lines (MILKDROP_PRESET_VERSION, PSVERSION*), a
[preset00] section, then key=value lines.  Multi-line code blocks are split
across numbered keys (per_frame_1=, per_frame_2=, ...; shader lines are
prefixed with a backtick).  Wave/shape sub-objects use wavecode_N_*/
shapecode_N_* for params and wave_N_per_point1=/shape_N_per_frame1= for code.
"""
import re
from pathlib import Path

_CODE_PREFIXES = (
    # (regex with one group for the line number, target attribute)
    (re.compile(r"^per_frame_init_(\d+)$"), "per_frame_init"),
    (re.compile(r"^per_frame_(\d+)$"), "per_frame"),
    (re.compile(r"^per_pixel_(\d+)$"), "per_pixel"),
)
_WAVE_CODE = re.compile(r"^wave_(\d+)_per_(point|frame)(\d+)$")
_WAVE_INIT = re.compile(r"^wave_(\d+)_init(\d+)$")
_SHAPE_CODE = re.compile(r"^shape_(\d+)_per_frame(\d+)$")
_SHAPE_INIT = re.compile(r"^shape_(\d+)_init(\d+)$")
_WAVE_PARAM = re.compile(r"^wavecode_(\d+)_(\w+)$")
_SHAPE_PARAM = re.compile(r"^shapecode_(\d+)_(\w+)$")
_SHADER = re.compile(r"^(warp|comp)_(\d+)$")


class MilkWave:
    def __init__(self):
        self.params = {}        # enabled, samples, sep, bSpectrum, scaling, r/g/b/a, ...
        self.init_code = []     # (n, line)
        self.per_frame = []
        self.per_point = []


class MilkShape:
    def __init__(self):
        self.params = {}        # enabled, sides, x, y, rad, ang, r/g/b/a, r2..., border_*
        self.init_code = []
        self.per_frame = []


class MilkFile:
    def __init__(self, path):
        self.path = str(path)
        self.name = Path(path).stem
        self.version = None          # MILKDROP_PRESET_VERSION
        self.psversion = 0           # max of PSVERSION* (0 = fixed-function)
        self.scalars = {}            # lowercased key -> float (or raw string)
        self.per_frame_init = []     # (line_no, code) tuples, joined later
        self.per_frame = []
        self.per_pixel = []
        self.waves = []              # grown on demand (MilkDrop3 allows up to 16)
        self.shapes = []
        self.has_warp_shader = False
        self.has_comp_shader = False

    def wave(self, idx):
        while len(self.waves) <= idx:
            self.waves.append(MilkWave())
        return self.waves[idx]

    def shape(self, idx):
        while len(self.shapes) <= idx:
            self.shapes.append(MilkShape())
        return self.shapes[idx]

    def code(self, attr):
        """Join numbered code lines (sorted) into one block."""
        lines = sorted(getattr(self, attr))
        return "\n".join(code for _n, code in lines)

    @staticmethod
    def _join(pairs):
        return "\n".join(code for _n, code in sorted(pairs))


def _maybe_float(s):
    try:
        return float(s)
    except ValueError:
        return s


def parse_milk_file(path):
    mf = MilkFile(path)
    with open(path, encoding="latin-1") as f:   # presets are 8-bit, often not UTF-8
        for raw in f:
            line = raw.rstrip("\r\n")
            if not line or line.startswith(("[", ";")):
                continue
            key, sep, value = line.partition("=")
            if not sep:
                continue
            key = key.strip()
            lkey = key.lower()

            if lkey == "milkdrop_preset_version":
                mf.version = _maybe_float(value)
                continue
            if lkey.startswith("psversion"):
                try:
                    mf.psversion = max(mf.psversion, int(float(value)))
                except ValueError:
                    pass
                continue

            m = _SHADER.match(lkey)
            if m:
                if m.group(1) == "warp":
                    mf.has_warp_shader = True
                else:
                    mf.has_comp_shader = True
                continue   # shader bodies intentionally ignored

            matched = False
            for rx, attr in _CODE_PREFIXES:
                m = rx.match(lkey)
                if m:
                    getattr(mf, attr).append((int(m.group(1)), value))
                    matched = True
                    break
            if matched:
                continue

            m = _WAVE_CODE.match(lkey)
            if m:
                idx, kind, n = int(m.group(1)), m.group(2), int(m.group(3))
                target = mf.wave(idx).per_point if kind == "point" else mf.wave(idx).per_frame
                target.append((n, value))
                continue
            m = _WAVE_INIT.match(lkey)
            if m:
                mf.wave(int(m.group(1))).init_code.append((int(m.group(2)), value))
                continue
            m = _SHAPE_CODE.match(lkey)
            if m:
                mf.shape(int(m.group(1))).per_frame.append((int(m.group(2)), value))
                continue
            m = _SHAPE_INIT.match(lkey)
            if m:
                mf.shape(int(m.group(1))).init_code.append((int(m.group(2)), value))
                continue
            m = _WAVE_PARAM.match(lkey)
            if m:
                mf.wave(int(m.group(1))).params[m.group(2).lower()] = _maybe_float(value)
                continue
            m = _SHAPE_PARAM.match(lkey)
            if m:
                mf.shape(int(m.group(1))).params[m.group(2).lower()] = _maybe_float(value)
                continue

            mf.scalars[lkey] = _maybe_float(value)
    return mf
