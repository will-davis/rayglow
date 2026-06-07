#!/usr/bin/env python3
"""Driven-Dissipative 2D Quantum Wavefunction Simulator (Schrödinger's Canvas).

Solves the Time-Dependent Schrödinger Equation (TDSE) in real time on a 256x32 grid:
i * hbar * d(psi)/dt = - (hbar^2 / 2m) * grad^2(psi) + V * psi

Features:
- Split-Step Fourier Method (SSFM) for unconditional numerical stability.
- Two moving emitters on the left side driving quantum wavepackets at slightly
  different frequencies, creating rich wave beating and interference.
- A vertical barrier with a double-slit in the middle (x ~ 80) and a grid of
  scattering pillars on the right.
- Driven-dissipative dynamics: localized source terms generate waves, while a
  global damping term dissipates energy to create an evolving steady-state.
- Autoranging normalization prevents brightness blowout or complete darkness.
- Smooth phase-to-RGB color palette mapping using continuous cosine palettes,
  slowly morphing over time.
- Direct blitting to the HUB75 LED matrix using Cython image conversion.
"""

import math
import sys
import time
from time import sleep

import numpy as np
from PIL import Image

# ---------------------------------------------------------------------------
# Matrix hardware config (Tuned to verified 256x32 rig)
# ---------------------------------------------------------------------------
from rgbmatrix import RGBMatrix, RGBMatrixOptions

options = RGBMatrixOptions()
options.rows = 32
options.cols = 64
options.chain_length = 4  # 256 width layout
options.parallel = 1
options.disable_hardware_pulsing = 0
options.gpio_slowdown = 5
options.brightness = 100
options.pwm_lsb_nanoseconds = 130
options.pwm_bits = 10
options.hardware_mapping = "adafruit-hat-pwm"

WIDTH = options.cols * options.chain_length   # 256
HEIGHT = options.rows                         # 32
print(f"Initializing Schrödinger's Canvas: {WIDTH}x{HEIGHT}")

# ---------------------------------------------------------------------------
# Physics Constants & Grid Setup
# ---------------------------------------------------------------------------
DT = 0.35                 # Time step
DAMPING = 0.003           # Dissipation factor per step (extreme low damping for maximum persistence)
SPEED_COEFF = 1.2         # Speed factor for kinetic dispersion (speedy propagation)

# Spatial grid coordinates
YS, XS = np.indices((HEIGHT, WIDTH))
XS = XS.astype(np.float32)
YS = YS.astype(np.float32)

# Momentum grid (Fourier space) for kinetic energy propagation
kx = 2.0 * np.pi * np.fft.fftfreq(WIDTH)
ky = 2.0 * np.pi * np.fft.fftfreq(HEIGHT)
KX, KY = np.meshgrid(kx, ky)
K2 = KX**2 + KY**2

# Kinetic operator propagator in Fourier space: exp(-i * SPEED_COEFF * k^2)
prop_k = np.exp(-1j * SPEED_COEFF * K2)

# ---------------------------------------------------------------------------
# Potential Barriers (The Obstacles)
# ---------------------------------------------------------------------------
V = np.zeros((HEIGHT, WIDTH), dtype=np.float32)

# 1. Double slit barrier in the middle
V[:, 78:82] = 20.0
# Slit openings (empty potential)
V[9:13, 78:82] = 0.0
V[19:23, 78:82] = 0.0

# 2. Circular scattering pillars on the right
posts = [
    (125, 7, 3.0),
    (125, 25, 3.0),
    (165, 16, 3.5),
    (205, 7, 3.0),
    (205, 25, 3.0),
    (235, 16, 3.0)
]
for px, py, pr in posts:
    mask = (XS - px)**2 + (YS - py)**2 <= pr**2
    V[mask] = 20.0

# Potential propagator in real space: exp(-i * dt * V / hbar)
prop_v = np.exp(-1j * (DT / 1.0) * V)

# Detect boundaries for visual rendering (to draw glowing outlines around obstacles)
# Shift in all 4 directions to find border pixels
is_obstacle = V > 0.0
boundary = is_obstacle & ~(
    np.roll(is_obstacle, 1, axis=0) &
    np.roll(is_obstacle, -1, axis=0) &
    np.roll(is_obstacle, 1, axis=1) &
    np.roll(is_obstacle, -1, axis=1)
)

# ---------------------------------------------------------------------------
# Cosine-based Color Palettes (Inigo Quilez formula)
# ---------------------------------------------------------------------------
# Each palette consists of bias (a), amplitude (b), and phase offset (c) vectors.
PALETTES = [
    # 0. Cosmic Violet (magenta, deep indigo, bright teal)
    {"a": np.array([0.5, 0.25, 0.6], dtype=np.float32),
     "b": np.array([0.5, 0.45, 0.4], dtype=np.float32),
     "c": np.array([0.0, 0.15, 0.30], dtype=np.float32)},
    # 1. Northern Lights (electric cyan, mint green, deep blue)
    {"a": np.array([0.2, 0.5, 0.5], dtype=np.float32),
     "b": np.array([0.3, 0.5, 0.4], dtype=np.float32),
     "c": np.array([0.8, 0.05, 0.20], dtype=np.float32)},
    # 2. Solar Flare (crimson, gold/amber, deep violet)
    {"a": np.array([0.65, 0.35, 0.2], dtype=np.float32),
     "b": np.array([0.35, 0.35, 0.2], dtype=np.float32),
     "c": np.array([0.05, 0.15, 0.45], dtype=np.float32)},
    # 3. Opal Nebula (pearl pink, soft blue, subtle gold)
    {"a": np.array([0.6, 0.55, 0.7], dtype=np.float32),
     "b": np.array([0.3, 0.35, 0.3], dtype=np.float32),
     "c": np.array([0.0, 0.20, 0.40], dtype=np.float32)}
]

# ---------------------------------------------------------------------------
# Simulation State Initialization
# ---------------------------------------------------------------------------
psi = np.zeros((HEIGHT, WIDTH), dtype=np.complex64)
max_density_smooth = 0.05  # Smooth running max for autoranging
sim_time = 0.0             # Physical simulation time clock
emitter1_phase = 0.0       # Accumulated phase for emitter 1
emitter2_phase = 0.0       # Accumulated phase for emitter 2

# Emitter tracking variables
EMITTER_SIGMA = 2.5

# ---------------------------------------------------------------------------
# Main Loop Setup
# ---------------------------------------------------------------------------
matrix = RGBMatrix(options=options)
canvas = matrix.CreateFrameCanvas()

print("Quantum Wave Simulation online. Emitters are broadcasting...")
start_time = time.time()
target_dt = 1.0 / 60.0

try:
    while True:
        t0 = time.time()
        elapsed = t0 - start_time
        sim_time += DT  # Advance simulation time clock

        # -------------------------------------------------------------------
        # 1. Physics Step (Split-Step Fourier Method)
        # -------------------------------------------------------------------
        # Apply half-step potential propagator
        psi *= prop_v

        # Transform to momentum space
        psi_k = np.fft.fft2(psi)

        # Apply kinetic operator propagator
        psi_k *= prop_k

        # Transform back to real space
        psi = np.fft.ifft2(psi_k)

        # -------------------------------------------------------------------
        # 2. Dynamic Driven-Dissipative Sources
        # -------------------------------------------------------------------
        # Emitters speed boosted to create Doppler shift wakes
        xs1 = 28.0 + 16.0 * math.sin(sim_time * 0.13)
        ys1 = 12.0 + 7.0 * math.sin(sim_time * 0.28)
        dist1_sq = (XS - xs1)**2 + (YS - ys1)**2
        src1 = np.exp(-dist1_sq / (2.0 * EMITTER_SIGMA**2))
        
        # Emitter 2
        xs2 = 42.0 + 16.0 * math.cos(sim_time * 0.10)
        ys2 = 20.0 + 7.0 * math.sin(sim_time * 0.22 + 0.8)
        dist2_sq = (XS - xs2)**2 + (YS - ys2)**2
        src2 = np.exp(-dist2_sq / (2.0 * EMITTER_SIGMA**2))

        # Dynamic frequency modulation (prevents forming static standing waves)
        omega1 = 0.90 + 0.40 * math.sin(sim_time * 0.03)
        omega2 = 1.15 + 0.40 * math.cos(sim_time * 0.04)

        # Integrate phases over time to avoid phase discontinuities
        emitter1_phase += omega1 * DT
        emitter2_phase += omega2 * DT

        # Dynamic amplitude breathing (pulses the emitters)
        amp1 = 7.0 * (0.55 + 0.45 * math.sin(sim_time * 0.05))
        amp2 = 7.0 * (0.55 + 0.45 * math.cos(sim_time * 0.07))

        # Complex driving source term S (boosted amplitude)
        S = (
            amp1 * src1 * np.exp(1j * emitter1_phase) +
            amp2 * src2 * np.exp(1j * emitter2_phase)
        )

        # Apply damping (dissipation) and inject source (driving)
        psi = psi * (1.0 - DAMPING) + S * DT

        # -------------------------------------------------------------------
        # 3. Visual Rendering & Normalization
        # -------------------------------------------------------------------
        # Calculate local probability density |psi|^2 and phase angle arg(psi)
        density = np.abs(psi)**2
        phase = np.angle(psi)  # Ranges in [-pi, pi]

        # Autoranging normalization using a running smooth maximum
        flat = density.ravel()
        idx = int(0.998 * (flat.size - 1))
        current_max = np.partition(flat, idx)[idx]
        if current_max > 0.0:
            max_density_smooth = 0.97 * max_density_smooth + 0.03 * current_max
        norm_density = np.clip(density / (max_density_smooth + 1e-6), 0.0, 1.0)

        # Apply spatial gamma scaling to lift weaker waves in dark areas
        brightness = norm_density ** 0.80

        # Cycle and morph color palettes over time
        t_cycle = elapsed * 0.04
        idx1 = int(t_cycle) % len(PALETTES)
        idx2 = (idx1 + 1) % len(PALETTES)
        blend = t_cycle % 1.0
        # Smooth Hermite interpolation curve
        blend = 3.0 * blend**2 - 2.0 * blend**3

        # Blend palette vectors
        a = (1.0 - blend) * PALETTES[idx1]["a"] + blend * PALETTES[idx2]["a"]
        b = (1.0 - blend) * PALETTES[idx1]["b"] + blend * PALETTES[idx2]["b"]
        c = (1.0 - blend) * PALETTES[idx1]["c"] + blend * PALETTES[idx2]["c"]

        # Map phase to [0.0, 1.0] cycle for color formulas
        phase_normalized = (phase + np.pi) / (2.0 * np.pi)

        # Vectorized RGB calculation using cosine palette parameters
        r_wave = a[0] + b[0] * np.cos(2.0 * np.pi * (phase_normalized + c[0]))
        g_wave = a[1] + b[1] * np.cos(2.0 * np.pi * (phase_normalized + c[1]))
        b_wave = a[2] + b[2] * np.cos(2.0 * np.pi * (phase_normalized + c[2]))

        # Apply wave intensity to color
        r = r_wave * brightness
        g = g_wave * brightness
        b = b_wave * brightness

        # Combine into RGB tensor (H, W, 3) in [0, 1] range
        rgb = np.stack([r, g, b], axis=-1)
        rgb = np.clip(rgb, 0.0, 1.0)

        # -------------------------------------------------------------------
        # 4. Obstacle Rendering Overlay
        # -------------------------------------------------------------------
        # Draw physical obstacles as a dim grey and boundary edge as warm white/gold
        rgb[is_obstacle] = [0.07, 0.06, 0.08]
        rgb[boundary] = [0.22, 0.18, 0.16]

        # Convert to final 8-bit array for PIL Image blitting
        frame_bytes = (rgb * 255.0).astype(np.uint8)
        image = Image.fromarray(frame_bytes, "RGB")

        canvas.SetImage(image)
        canvas = matrix.SwapOnVSync(canvas)

        # Enforce frame pace regulation
        loop_time = time.time() - t0
        if loop_time < target_dt:
            sleep(target_dt - loop_time)

except KeyboardInterrupt:
    print("\nExiting Schrödinger's Canvas. Clearing display...")
    canvas.Clear()
    matrix.Clear()
    sys.exit(0)
