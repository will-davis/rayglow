# experiments/

Standalone LED-matrix sketches that predate (and run independently of) the
RayGLow renderer. Each is a self-contained script that talks to the panel
directly via `rgbmatrix` (hzeller's library) — no `rayglow` import, no audio
feed, no shared engine. They're kept here because they're part of the project's
story and a handy grab-bag of pixel-pushing techniques on this rig.

Heads-up: most were written for a **320×32** panel chain (5 panels); the
renderer settled on 256×32 (`CHAIN=4`). Geometry is hardcoded per script — adjust
the `RGBMatrixOptions` at the top before running on the current rig.

Run directly on the Pi (root for GPIO):

```fish
sudo ~/rgbvenv/bin/python experiments/plasma.py
```

| script | what it does |
|---|---|
| `plasma.py` | interfering-sine plasma through a phase-shifting RGB palette |
| `flocking.py` | boids |
| `slime.py` | Physarum / slime-mold agents |
| `gray-scott.py` | Gray–Scott reaction–diffusion |
| `navier-stokes.py` | fluid sim |
| `schrodinger.py` | Schrödinger / wavefunction evolution |
| `phase-flow.py` | phase-space / vector-field flow |
| `fields.py` | field visualization |
| `langton.py` | Langton's ant |
| `starfall-old.py` | starfield |
| `caustic-waves.py` | water-caustic interference |
| `tuner.py` | a panel utility/tuning sketch |
| `ollama.py` | turns the panel into a 3-line terminal streaming from an ollama server |

These are not maintained alongside the renderer and may assume old geometry or
deps. Treat them as reference sketches, not products.
