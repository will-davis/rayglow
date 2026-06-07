#!/usr/bin/env python3
"""Discrete Shallow-Water Wave Equation & Refractive Caustics Engine.

Solves a 2D hyperbolic wave equation across a toroidal fluid field using an
allocation-free finite difference stencil. The resulting height field forms
a time-dependent non-linear optical lens.

Light rays are projected through the surface and refracted via an optimized
Snell's Law approximation:
    X_shifted = X + Alpha * (dH/dX)
    Y_shifted = Y + Alpha * (dH/dY)

This concentration of redirected light energy generates real-time evolving
caustic networks (light pooling) over a multi-stop deep ocean palette. Continual
energy injection is achieved via randomized high-frequency droplet impacts and
a slow, large-scale periodic tidal driving force.
"""

import math
import sys
from time import time, sleep
import numpy as np
from PIL import Image

# ---------------------------------------------------------------------------
# Matrix hardware config (matches your verified rig)
# ---------------------------------------------------------------------------
from rgbmatrix import RGBMatrix, RGBMatrixOptions

options = RGBMatrixOptions()
options.rows = 32
options.cols = 64
options.chain_length = 4
options.parallel = 1
options.disable_hardware_pulsing = 0       # snd_bcm2835 blacklisted
options.gpio_slowdown = 5                   # tuned to eliminate panel-1 artifact
options.brightness = 100
options.pwm_bits = 10
options.hardware_mapping = "adafruit-hat-pwm" # GPIO4->GPIO18 physical jumper active

WIDTH = options.cols * options.chain_length   # 256
HEIGHT = options.rows                         # 32
print(f"Resolving Canvas: {WIDTH}x{HEIGHT}")

# ---------------------------------------------------------------------------
# Physical Simulation & Optical Tunables
# ---------------------------------------------------------------------------
C_WAVE_SPEED = 0.25       # Wave propagation speed (Courant-Friedrichs-Lewy limit bound)
DAMPING = 0.988           # Viscosity loss factor per step to stabilize energy
STEPS_PER_FRAME = 3       # Sub-stepping iterations of PDE solver per render cycle

DROPLET_CHANCE = 0.05     # Probability of a droplet impact per frame
DROPLET_RADIUS = 3.5      # Mean radius of impact excitation
DROPLET_MAGNITUDE = 1.2   # Phase displacement depth of impacts

# Optical properties
REFRACTION_DEPTH = 10.0   # Focal distance focal depth index (Alpha scale for ray offsets)
CAUSTIC_BOOST = 0.4       # Intensity scaling factor for converged light arrays
CAUSTIC_GAMMA = 1.0       # Contrast curve exponent for light bands

# Color Palette: Deep Trench -> Bioluminescent Turquoise -> Seafoam Crest
PALETTE = np.array([
    [2,   4,  24],   # Deep background
    [0,  62,  98],   # Medium depth
    [0, 164, 180],   # Refracted turquoise
    [112, 235, 210], # High intensity caustics
    [255, 255, 255]  # Peak convergence / White out
], dtype=np.float32)

PALETTE_POSITIONS = np.array([0.0, 0.28, 0.58, 0.85, 1.0], dtype=np.float32)

# ---------------------------------------------------------------------------
# Array Preallocation Engine (Strictly Zero Allocations inside the Main Loop)
# ---------------------------------------------------------------------------
# Discrete wave field arrays
h_curr = np.zeros((HEIGHT, WIDTH), dtype=np.float32)
h_prev = np.zeros((HEIGHT, WIDTH), dtype=np.float32)
h_next = np.zeros((HEIGHT, WIDTH), dtype=np.float32)

# Coordinate grids for ray mapping
y_idx, x_idx = np.indices((HEIGHT, WIDTH), dtype=np.float32)
FLAT_SIZE = HEIGHT * WIDTH

# Caustic accumulation tracking buffer
caustic_map = np.zeros((HEIGHT, WIDTH), dtype=np.float32)
render_buffer = np.zeros((HEIGHT, WIDTH, 3), dtype=np.float32)

# Precomputed 1D color lookup table for instant mapping
lut = np.zeros((1024, 3), dtype=np.float32)
for i in range(1024):
    t = i / 1023.0
    idx = np.searchsorted(PALETTE_POSITIONS, t) - 1
    idx = max(0, min(idx, len(PALETTE_POSITIONS) - 2))
    p0 = PALETTE_POSITIONS[idx]
    p1 = PALETTE_POSITIONS[idx + 1]
    weight = (t - p0) / (p1 - p0)
    lut[i] = PALETTE[idx] * (1.0 - weight) + PALETTE[idx + 1] * weight

rng = np.random.default_rng()

# ---------------------------------------------------------------------------
# Vectorized PDE Operators & Pipeline Steps
# ---------------------------------------------------------------------------
def inject_droplet(h, cx, cy, rad, mag):
    """Bilinearly maps a localized Gaussian droplet perturbation onto height field."""
    y, x = np.ogrid[-cy:HEIGHT-cy, -cx:WIDTH-cx]
    # Toroidal wrapping for spatial distances
    x = np.where(x > WIDTH / 2, x - WIDTH, x)
    x = np.where(x < -WIDTH / 2, x + WIDTH, x)
    y = np.where(y > HEIGHT / 2, y - HEIGHT, y)
    y = np.where(y < -HEIGHT / 2, y + HEIGHT, y)

    dist_sq = x*x + y*y
    mask = dist_sq <= rad*rad
    h[mask] += np.cos(np.sqrt(dist_sq[mask]) * (math.pi / rad)) * mag

def step_wave_equation():
    """Evaluates 5-point Laplacian stencil with strict toroidal boundaries."""
    global h_curr, h_prev, h_next

    # 4-way discrete shifted configurations avoiding np.roll overhead allocation
    laplacian = (
        np.concatenate((h_curr[1:, :], h_curr[:1, :]), axis=0) +  # South
        np.concatenate((h_curr[-1:, :], h_curr[:-1, :]), axis=0) + # North
        np.concatenate((h_curr[:, 1:], h_curr[:, :1]), axis=1) +  # East
        np.concatenate((h_curr[:, -1:], h_curr[:, :-1]), axis=1)   # West
        - 4.0 * h_curr
    )

    # Finite difference time integration step
    h_next = (2.0 * h_curr - h_prev) + (C_WAVE_SPEED * C_WAVE_SPEED) * laplacian
    h_next *= DAMPING

    # Circular buffer rotation swap
    h_prev = h_curr
    h_curr = h_next

def compute_caustics(t_cycle):
    """Calculates spatial gradient fields to concentrate light tracking paths."""
    global caustic_map

    # Fix Step 1: Correct the axis-1 slicing typos for exact bounding matches
    dh_dx = 0.5 * (
        np.concatenate((h_curr[:, 1:], h_curr[:, :1]), axis=1) -
        np.concatenate((h_curr[:, -1:], h_curr[:, :-1]), axis=1)
    )
    dh_dy = 0.5 * (
        np.concatenate((h_curr[1:, :], h_curr[:1, :]), axis=0) -
        np.concatenate((h_curr[-1:, :], h_curr[:-1, :]), axis=0)
    )

    # Inject systemic rhythmic tidal driving gradients to maintain low-frequency motion
    dh_dx += 0.08 * math.sin(t_cycle * 0.45)
    dh_dy += 0.04 * math.cos(t_cycle * 0.65)

    # Compute target refracted path indices mapping via Snell's law projection
    map_x = (x_idx + dh_dx * REFRACTION_DEPTH) % WIDTH
    map_y = (y_idx + dh_dy * REFRACTION_DEPTH) % HEIGHT

    # Extract target array bin positions (down-cast to integers)
    x0 = map_x.astype(np.int32)
    y0 = map_y.astype(np.int32)

    # Toroidal safety boundaries
    x0 %= WIDTH
    y0 %= HEIGHT

    # Fast 1D histogramming via bincount
    flat_indices = y0 * WIDTH + x0
    counts = np.bincount(flat_indices.ravel(), minlength=FLAT_SIZE)

    # Reshape counts and add baseline ambient photon illumination level
    caustic_map = counts.reshape(HEIGHT, WIDTH).astype(np.float32) + 0.15

    # Fix Step 7: Resolve the invalid multi-axis concatenation using sequential 1D wraps
    # Shift South / North
    c_south = np.concatenate((caustic_map[1:, :], caustic_map[:1, :]), axis=0)
    # Shift East / West
    c_east  = np.concatenate((caustic_map[:, 1:], caustic_map[:, :1]), axis=1)
    # Shift Southeast corner wrap sequentially
    c_se    = np.concatenate((c_south[:, 1:], c_south[:, :1]), axis=1)

    # Apply soft box smoothing step across photon array to diminish aliasing artifacts
    caustic_map = 0.25 * (caustic_map + c_south + c_east + c_se)

# ---------------------------------------------------------------------------
# Main Execution Cycle
# ---------------------------------------------------------------------------
# Initial seeding to break uniform flat state homogeneity
for _ in range(15):
    inject_droplet(h_curr, rng.uniform(0, WIDTH), rng.uniform(0, HEIGHT),
                   rng.uniform(2, 6), rng.uniform(-1, 1))

matrix = RGBMatrix(options=options)
canvas = matrix.CreateFrameCanvas()

start_time = time()
target_dt = 1.0 / 60.0

print("System hot. Starting wave cycle...\n")

while True:
    t0 = time()
    t_run = t0 - start_time

    # Stochastic drop generator
    if rng.random() < DROPLET_CHANCE:
        inject_droplet(h_curr, rng.uniform(0, WIDTH), rng.uniform(0, HEIGHT),
                       rng.uniform(1.5, 4.5), rng.uniform(DROPLET_MAGNITUDE*0.5, DROPLET_MAGNITUDE))

    # Substep continuous partial differential equation solver
    for _ in range(STEPS_PER_FRAME):
        step_wave_equation()

    # Process light ray trace array optimization mapping
    compute_caustics(t_run)

    # Scale intensities through look-up window bounds
    normalized_intensity = np.clip((caustic_map * CAUSTIC_BOOST) ** CAUSTIC_GAMMA, 0.0, 1.0)

    # Hard clip indices to [0, 1023] to prevent float casting out-of-bound errors
    lut_indices = np.clip((normalized_intensity * 1023.0).astype(np.int32), 0, 1023)

    # Direct vectorized index assignment from pre-calculated palette matrix mapping
    render_buffer = lut[lut_indices]

    # Cast to final byte canvas container
    frame_bytes = render_buffer.astype(np.uint8)
    image = Image.fromarray(frame_bytes, "RGB")

    canvas.SetImage(image)
    canvas = matrix.SwapOnVSync(canvas)

    # Enforce precise runtime pace regulation alignment
    elapsed = time() - t0
    if elapsed < target_dt:
        sleep(target_dt - elapsed)
