"""dotmilk: load and run real MilkDrop .milk preset files on the milk engine.

Pipeline: parser.py reads the INI-ish file format; eel.py transpiles the
NS-EEL equation code (per_frame / per_pixel) to Python/NumPy; runtime.py
wraps it all as a MilkPreset that plugs into the engine's preset interface.

Shader-era presets (PSVERSION>=2) run their EQUATIONS faithfully — in real
MilkDrop the equations drive all the warp motion and the warp shader only
recolors the resample — but warp/comp shader bodies themselves are ignored
(we render as if the preset used the default shaders).
"""
from .parser import parse_milk_file
