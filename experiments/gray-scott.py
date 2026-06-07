#!/usr/bin/env python3
"""Gray-Scott reaction-diffusion for a 256x32 HUB75 LED matrix.

Two virtual chemicals U and V diffuse and react on a toroidal grid:

    dU/dt = Du * lap(U) - U*V^2 + F*(1 - U)
    dV/dt = Dv * lap(V) + U*V^2 - (F + k)*V

V eats U and feeds itself (the U*V^2 term); F replenishes U and k drains V.
Depending on (F, k) the steady state is spots that split like dividing cells
("mitosis"), coral-like mazes, wriggling worms, or traveling waves — the
classic Turing patterns. This script slowly tours a list of known-good
(F, k) regimes, smoothly interpolating between them, so the pattern type
itself keeps evolving over minutes.

Visual pipeline (tuned by eye via the --headless contact sheet):
  * simulate at 2x supersample (512x64), box-downsample to 256x32 for a
    smooth, antialiased organic look on the coarse LED grid
  * normalize V with a slow-adapting percentile window, map through a
    curated multi-stop color palette (palettes crossfade every couple of
    minutes)
  * emboss lighting: V's gradient becomes a surface normal field, lit by a
    slowly orbiting light (diffuse + specular), so the patterns read as
    glossy 3D relief instead of flat blobs
  * occasional V "droplets" splash in as rings of new growth, and an
    extinction watchdog reseeds if the pattern ever dies out

Performance: the inner loop is cv2.filter2D (opencv-python-headless) for the
laplacian plus fully preallocated in-place numpy for the reaction terms —
1.2 ms/step at 512x64 on the Pi 4 vs 5.6 ms for the naive np.roll version,
which is what makes 2x supersampling at ~50 fps possible at all.

Run on hardware:   sudo ~/rgbvenv/bin/python gray-scott.py
Preview headless:  ~/rgbvenv/bin/python gray-scott.py --headless [outdir]
  (no root / no matrix needed: runs a compressed tour, dumps per-regime
   snapshots + a contact sheet PNG, prints perf and liveliness stats)
"""

import math
import sys
from time import time, sleep

import cv2
import numpy as np
from PIL import Image

HEADLESS = "--headless" in sys.argv

# ---------------------------------------------------------------------------
# Matrix hardware config (matches the rest of this rig — see CLAUDE.md / memory)
# ---------------------------------------------------------------------------
if HEADLESS:
    WIDTH, HEIGHT = 256, 32
else:
    from rgbmatrix import RGBMatrix, RGBMatrixOptions

    options = RGBMatrixOptions()
    options.rows = 32
    options.cols = 64
    options.chain_length = 4
    options.parallel = 1
    options.disable_hardware_pulsing = 0       # snd_bcm2835 is blacklisted
    options.gpio_slowdown = 5                   # tuned: 5 + lsb 130 ~eliminates panel-1 artifact
    options.brightness = 100
    options.pwm_bits = 10
    options.hardware_mapping = "adafruit-hat-pwm"  # GPIO4->GPIO18 jumper installed

    WIDTH = options.cols * options.chain_length   # 256
    HEIGHT = options.rows                         # 32
print(WIDTH, "x", HEIGHT)

# ---------------------------------------------------------------------------
# Simulation tunables
# ---------------------------------------------------------------------------
SS = 1                       # supersample factor (sim grid = SS * display grid).
                             # 1 (native) is deliberate: Gray-Scott's pattern
                             # wavelength is ~7 sim px, so native res gives
                             # chunky ~7-display-px features; at SS=2 they
                             # shrink to ~3 px and read as noise on the panel.
SW, SH = WIDTH * SS, HEIGHT * SS

DU, DV = 0.16, 0.08          # diffusion rates (standard pixel-grid Gray-Scott)
DT = 1.0                     # Euler step (stable with the 9-point laplacian)
STEPS_PER_FRAME = 12         # sim steps per displayed frame (evolution speed)

# The (F, k) tour: name, feed, kill. Ordered for short hops in parameter
# space; each is held HOLD_SEC then blended to the next over BLEND_SEC.
TOUR = [
    ("waves",        0.014, 0.045),   # traveling waves, annihilate on contact
    ("moving spots", 0.014, 0.054),   # gliding solitons
    ("maze",         0.029, 0.057),   # coral / labyrinth growth
    ("holes",        0.039, 0.058),   # stripes punched with dark holes
                                      # (0.034/0.056 "chaos+holes" saturates
                                      #  into a flat uniform field — avoid)
    ("default",      0.037, 0.060),   # classic coral reef
    ("mitosis",      0.0367, 0.0649), # spots that split like dividing cells
    ("u-skate",      0.062, 0.0609),  # stable gliders / loops
    ("worms",        0.078, 0.061),   # fat wriggling worms
]
HOLD_SEC = 35.0              # time spent inside each regime
BLEND_SEC = 12.0             # smooth (F,k) interpolation between regimes

DROPLET_SEC = 18.0           # splash a couple of fresh V droplets this often
DROPLET_N = 2                # droplets per splash
DROPLET_R = (2, 5)           # droplet radius range (sim px)
DEAD_VMAX = 0.02             # if V.max() falls below this, the dish is dead ->
DEAD_RESEED_N = 10           #   reseed with this many droplets

# ---------------------------------------------------------------------------
# Render tunables
# ---------------------------------------------------------------------------
TARGET_FPS = 60

# Adaptive normalization: t = (v - lo) / (hi - lo), lo/hi are EMA-smoothed
# percentiles so every regime uses the full palette range.
NORM_PCT = (2.0, 98.5)       # percentiles tracked for lo / hi
NORM_EMA = 0.03              # per-frame adaptation rate (smaller = steadier)
NORM_MIN_SPAN = 0.10         # floor on (hi - lo): a near-uniform field renders
                             # dim instead of having its noise amplified to
                             # full palette range (the "flat blowout" failure)
T_GAMMA = 0.85               # <1 lifts midtones a touch before the palette

# Multi-stop palettes (position 0..1 -> RGB).  Crossfaded in sequence.
PALETTES = [
    ("abyss", [          # bioluminescent deep sea: navy -> teal -> mint -> amber
        (0.00, (0, 0, 6)),
        (0.30, (8, 28, 70)),
        (0.52, (0, 140, 160)),
        (0.72, (90, 225, 170)),
        (0.88, (250, 200, 95)),
        (1.00, (255, 248, 215)),
    ]),
    ("ember", [          # lava: black -> oxblood -> orange -> gold -> white
        (0.00, (2, 0, 2)),
        (0.32, (70, 8, 14)),
        (0.55, (200, 55, 10)),
        (0.75, (255, 150, 20)),
        (0.90, (255, 220, 90)),
        (1.00, (255, 250, 220)),
    ]),
    ("orchid", [         # ultraviolet: indigo -> violet -> magenta -> pink ice
        (0.00, (1, 0, 8)),
        (0.32, (35, 10, 90)),
        (0.55, (120, 25, 170)),
        (0.75, (230, 60, 160)),
        (0.90, (255, 150, 190)),
        (1.00, (255, 235, 245)),
    ]),
    ("verdant", [        # moss & gold: black -> forest -> green -> chartreuse
        (0.00, (0, 3, 0)),
        (0.32, (8, 50, 22)),
        (0.55, (30, 140, 40)),
        (0.75, (140, 220, 50)),
        (0.90, (240, 240, 110)),
        (1.00, (255, 255, 225)),
    ]),
]
PALETTE_HOLD_SEC = 90.0      # time on each palette
PALETTE_BLEND_SEC = 12.0     # crossfade duration

# Emboss lighting: V's gradient -> normals, lit by an orbiting light.
RELIEF = 14.0                # normal-map strength (higher = deeper relief)
LIGHT_ORBIT_SEC = 47.0       # seconds per full orbit of the light azimuth
LIGHT_ELEV = 0.9             # light elevation (rad); higher = flatter lighting
AMBIENT = 0.42               # base illumination
DIFFUSE = 0.62               # diffuse strength
SPEC_STR = 0.55              # specular highlight strength
SPEC_SHIN = 14.0             # specular exponent (higher = tighter highlights)
SPEC_COLOR = np.array([255.0, 245.0, 235.0], dtype=np.float32)  # warm-white

OUT_GAMMA = 1.15             # final gamma; >1 deepens blacks on the panel


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------
rng = np.random.default_rng()


# 9-point laplacian kernel (0.2 cardinal, 0.05 diagonal — better isotropy
# than 5-point, so spots stay round instead of going square).
LAP_KERN = np.array([[0.05, 0.20, 0.05],
                     [0.20, -1.0, 0.20],
                     [0.05, 0.20, 0.05]], dtype=np.float32)

# Preallocated scratch for the fused sim step (no per-step allocations).
_pad = np.empty((SH + 2, SW + 2), dtype=np.float32)
_lapU = np.empty((SH, SW), dtype=np.float32)
_lapV = np.empty((SH, SW), dtype=np.float32)
_uvv = np.empty((SH, SW), dtype=np.float32)
_tmp = np.empty((SH, SW), dtype=np.float32)


def lap9(Z, out):
    """9-point laplacian with toroidal wrap, via cv2 (SIMD; ~10x np.roll)."""
    cv2.copyMakeBorder(Z, 1, 1, 1, 1, cv2.BORDER_WRAP, dst=_pad)
    np.copyto(out, cv2.filter2D(_pad, -1, LAP_KERN)[1:-1, 1:-1])


def splash(U, V, n, r_range=DROPLET_R):
    """Drop n circular blobs of V into the dish (rings of new growth)."""
    ys, xs = np.mgrid[0:SH, 0:SW]
    for _ in range(n):
        cx, cy = rng.integers(0, SW), rng.integers(0, SH)
        r = rng.integers(r_range[0], r_range[1] + 1)
        # toroidal distance so droplets wrap cleanly at the edges
        dx = np.minimum(np.abs(xs - cx), SW - np.abs(xs - cx))
        dy = np.minimum(np.abs(ys - cy), SH - np.abs(ys - cy))
        mask = dx * dx + dy * dy <= r * r
        V[mask] = 0.5
        U[mask] = 0.25


def fresh_dish():
    U = np.ones((SH, SW), dtype=np.float32)
    V = np.zeros((SH, SW), dtype=np.float32)
    splash(U, V, DEAD_RESEED_N)
    # a sprinkle of noise breaks symmetry so blobs don't grow perfectly round
    V += (rng.random((SH, SW), dtype=np.float32) * 0.02)
    return U, V


def sim_steps(U, V, F, k, steps):
    """Fused, allocation-free Gray-Scott steps:
        U += DT*(DU*lap(U) - U*V^2 + F*(1-U))
        V += DT*(DV*lap(V) + U*V^2 - (F+k)*V)
    (out= everywhere — augmented ops on the module-level scratch would
    rebind them as locals and crash, hence the explicit np.* calls)."""
    for _ in range(steps):
        lap9(U, _lapU)
        lap9(V, _lapV)
        np.multiply(V, V, out=_uvv)
        np.multiply(_uvv, U, out=_uvv)                 # uvv = U*V^2
        np.multiply(_lapU, DT * DU, out=_tmp)
        np.subtract(_tmp, _uvv, out=_tmp)
        np.multiply(U, 1.0 - DT * F, out=U)            # the F*(1-U) feed term,
        np.add(U, _tmp, out=U)                         #   folded into U's scale
        np.add(U, DT * F, out=U)
        np.multiply(_lapV, DT * DV, out=_tmp)
        np.add(_tmp, _uvv, out=_tmp)
        np.multiply(V, 1.0 - DT * (F + k), out=V)      # the (F+k)*V kill term
        np.add(V, _tmp, out=V)


def tour_params(t):
    """(F, k) at tour-time t: hold each regime, smoothstep-blend to the next."""
    seg = HOLD_SEC + BLEND_SEC
    cycle = seg * len(TOUR)
    t = t % cycle
    i = int(t // seg)
    frac = (t % seg)
    _, f0, k0 = TOUR[i]
    if frac < HOLD_SEC:
        return f0, k0, TOUR[i][0]
    _, f1, k1 = TOUR[(i + 1) % len(TOUR)]
    x = (frac - HOLD_SEC) / BLEND_SEC
    x = x * x * (3.0 - 2.0 * x)          # smoothstep
    return f0 + (f1 - f0) * x, k0 + (k1 - k0) * x, TOUR[i][0]


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def build_lut(stops):
    """256-entry float32 RGB LUT from (pos, (r,g,b)) stops."""
    pos = np.array([p for p, _ in stops], dtype=np.float32)
    cols = np.array([c for _, c in stops], dtype=np.float32)
    x = np.linspace(0.0, 1.0, 256, dtype=np.float32)
    return np.stack([np.interp(x, pos, cols[:, ch]) for ch in range(3)],
                    axis=1).astype(np.float32)


LUTS = [build_lut(stops) for _, stops in PALETTES]


def palette_lut(t):
    """Current 256x3 LUT: hold each palette, crossfade to the next."""
    seg = PALETTE_HOLD_SEC + PALETTE_BLEND_SEC
    t = t % (seg * len(LUTS))
    i = int(t // seg)
    frac = t % seg
    if frac < PALETTE_HOLD_SEC:
        return LUTS[i]
    x = (frac - PALETTE_HOLD_SEC) / PALETTE_BLEND_SEC
    x = x * x * (3.0 - 2.0 * x)
    return LUTS[i] * (1.0 - x) + LUTS[(i + 1) % len(LUTS)] * x


class Normalizer:
    """EMA-smoothed percentile window so every regime fills the palette."""

    def __init__(self):
        self.lo = None
        self.hi = None

    def __call__(self, v):
        lo = float(np.percentile(v, NORM_PCT[0]))
        hi = float(np.percentile(v, NORM_PCT[1]))
        if self.hi is None or self.hi - self.lo < 1e-6:
            self.lo, self.hi = lo, hi
        else:
            self.lo += NORM_EMA * (lo - self.lo)
            self.hi += NORM_EMA * (hi - self.hi)
        span = max(self.hi - self.lo, NORM_MIN_SPAN)
        return np.clip((v - self.lo) / span, 0.0, 1.0)


norm = Normalizer()


def box3(Z):
    """Cheap 3x3 box blur (toroidal) — smooths V before taking normals."""
    n = np.roll(Z, -1, 0); s = np.roll(Z, 1, 0)
    return (Z + n + s + np.roll(Z, -1, 1) + np.roll(Z, 1, 1)
            + np.roll(n, -1, 1) + np.roll(n, 1, 1)
            + np.roll(s, -1, 1) + np.roll(s, 1, 1)) * (1.0 / 9.0)


def render(V, t_now):
    """V field -> lit, palette-mapped (H, W, 3) uint8 frame."""
    # supersampled sim -> display res (box filter = antialiasing)
    v = V.reshape(HEIGHT, SS, WIDTH, SS).mean(axis=(1, 3))

    t = norm(v) ** T_GAMMA
    base = palette_lut(t_now)[(t * 255.0).astype(np.uint8)]   # (H, W, 3)

    # --- emboss lighting ---------------------------------------------------
    vs = box3(v)
    gx = (np.roll(vs, -1, 1) - np.roll(vs, 1, 1)) * 0.5
    gy = (np.roll(vs, -1, 0) - np.roll(vs, 1, 0)) * 0.5
    nx, ny, nz = -gx * RELIEF, -gy * RELIEF, np.float32(1.0)
    inv = 1.0 / np.sqrt(nx * nx + ny * ny + 1.0)
    nx, ny, nz = nx * inv, ny * inv, inv

    az = 2.0 * math.pi * (t_now / LIGHT_ORBIT_SEC)
    ce, se = math.cos(LIGHT_ELEV), math.sin(LIGHT_ELEV)
    lx, ly, lz = math.cos(az) * ce, math.sin(az) * ce, se
    diff = np.clip(nx * lx + ny * ly + nz * lz, 0.0, 1.0)

    # half-vector for specular (view = straight on, (0,0,1))
    hlen = math.sqrt(lx * lx + ly * ly + (lz + 1.0) ** 2)
    hx, hy, hz = lx / hlen, ly / hlen, (lz + 1.0) / hlen
    spec = np.clip(nx * hx + ny * hy + nz * hz, 0.0, 1.0) ** SPEC_SHIN
    spec *= t  # kill the flat-background sheen, keep highlights on the pattern

    rgb = (base * (AMBIENT + DIFFUSE * diff)[:, :, None]
           + (SPEC_STR * spec)[:, :, None] * SPEC_COLOR)
    rgb = 255.0 * (np.clip(rgb, 0.0, 255.0) / 255.0) ** OUT_GAMMA
    return rgb.astype(np.uint8)


# ---------------------------------------------------------------------------
# Headless preview: compressed tour -> per-regime snapshots + contact sheet
# ---------------------------------------------------------------------------
def run_headless():
    import os
    outdir = sys.argv[sys.argv.index("--headless") + 1] \
        if len(sys.argv) > sys.argv.index("--headless") + 1 else "/tmp/grayscott"
    os.makedirs(outdir, exist_ok=True)

    global HOLD_SEC, BLEND_SEC
    HOLD_SEC, BLEND_SEC = 10.0, 5.0          # compressed tour for preview
    frame_dt = 1.0 / TARGET_FPS

    U, V = fresh_dish()
    shots, stats = [], []
    seg = HOLD_SEC + BLEND_SEC
    total_frames = int(seg * len(TOUR) / frame_dt)
    snap_times = [i * seg + HOLD_SEC * 0.85 for i in range(len(TOUR))]
    next_snap = 0

    t_sim = t_render = 0.0
    last_drop = 0.0
    wall0 = time()
    for f in range(total_frames):
        vt = f * frame_dt                     # virtual clock
        F, k, name = tour_params(vt)

        w0 = time()
        sim_steps(U, V, F, k, STEPS_PER_FRAME)
        t_sim += time() - w0

        if vt - last_drop >= DROPLET_SEC:
            splash(U, V, DROPLET_N)
            last_drop = vt
        if V.max() < DEAD_VMAX:
            splash(U, V, DEAD_RESEED_N)

        w0 = time()
        frame = render(V, vt)
        t_render += time() - w0

        if next_snap < len(snap_times) and vt >= snap_times[next_snap]:
            img = Image.fromarray(frame, "RGB").resize(
                (WIDTH * 4, HEIGHT * 4), Image.NEAREST)
            shots.append((TOUR[next_snap][0], img))
            act = float(np.abs(V - box3(V)).mean())
            cov = float((norm(V.reshape(HEIGHT, SS, WIDTH, SS)
                              .mean(axis=(1, 3))) > 0.3).mean())
            stats.append((TOUR[next_snap][0], F, k, cov, V.max()))
            img.save(f"{outdir}/{next_snap}_{TOUR[next_snap][0].replace(' ', '_')}.png")
            next_snap += 1

    wall = time() - wall0
    print(f"{total_frames} frames in {wall:.1f}s "
          f"({total_frames / wall:.0f} fps headless)")
    print(f"  sim:    {t_sim / total_frames * 1000:.2f} ms/frame "
          f"({STEPS_PER_FRAME} steps @ {SW}x{SH})")
    print(f"  render: {t_render / total_frames * 1000:.2f} ms/frame")
    print(f"\n{'regime':>14} {'F':>7} {'k':>7} {'cov>0.3':>8} {'Vmax':>6}")
    for name, F, k, cov, vmax in stats:
        print(f"{name:>14} {F:>7.4f} {k:>7.4f} {cov:>8.2f} {vmax:>6.3f}")

    # contact sheet
    pad, label_h = 4, 14
    cell_w, cell_h = WIDTH * 4, HEIGHT * 4 + label_h
    sheet = Image.new("RGB", (cell_w + 2 * pad,
                              len(shots) * (cell_h + pad) + pad), (24, 24, 24))
    from PIL import ImageDraw
    draw = ImageDraw.Draw(sheet)
    for i, (name, img) in enumerate(shots):
        y = pad + i * (cell_h + pad)
        draw.text((pad + 2, y), name, fill=(200, 200, 200))
        sheet.paste(img, (pad, y + label_h))
    sheet.save(f"{outdir}/sheet.png")
    print(f"\ncontact sheet: {outdir}/sheet.png")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if HEADLESS:
    run_headless()
    sys.exit(0)

U, V = fresh_dish()

# Warm up the full sim+render path ONCE before RGBMatrix() drops root:
# np.percentile lazily imports numpy.ma on first call, and after the
# privilege drop the process can't read ~/rgbvenv (drwx------ /home/will),
# so any lazy import would die with ModuleNotFoundError mid-loop.
sim_steps(U, V, *TOUR[0][1:3], 1)
render(V, 0.0)
norm.lo = norm.hi = None        # reset the normalizer the warm-up just seeded

matrix = RGBMatrix(options=options)
canvas = matrix.CreateFrameCanvas()
print("Matrix initialized\n")
frame_dt = 1.0 / TARGET_FPS
start = time()
last_drop = 0.0

while True:
    t0 = time()
    t_now = t0 - start

    F, k, _ = tour_params(t_now)
    sim_steps(U, V, F, k, STEPS_PER_FRAME)

    # periodic droplet splashes + extinction watchdog
    if t_now - last_drop >= DROPLET_SEC:
        splash(U, V, DROPLET_N)
        last_drop = t_now
    if V.max() < DEAD_VMAX:
        splash(U, V, DEAD_RESEED_N)

    frame = render(V, t_now)
    canvas.SetImage(Image.fromarray(frame, "RGB"))
    canvas = matrix.SwapOnVSync(canvas)

    elapsed = time() - t0
    if elapsed < frame_dt:
        sleep(frame_dt - elapsed)
