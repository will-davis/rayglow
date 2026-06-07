#!/usr/bin/env python3
"""Multi-Species Physarum Polycephalum (Slime Mold) Emergent Transport Networks.

Simulates thousands of microscopic autonomous agents navigating a continuous 2D
toroidal field. Agents deposit species-specific pheromones, sample the field via
a three-pronged forward-looking sensor array, and steer toward the highest local
chemical concentration.

The interaction of thousands of independent sensory loops causes complex,
organic filaments, pulsing highway veins, and flowing web networks to emerge
from pure randomness.

Everything is fully vectorized over the agent arrays using NumPy and blitted via
canvas.SetImage() for optimal execution speeds on the Raspberry Pi.
"""

import math
import sys
from time import time, sleep
import numpy as np
from PIL import Image

# ---------------------------------------------------------------------------
# Matrix hardware config (Matches verified 256x32 rig)
# ---------------------------------------------------------------------------
from rgbmatrix import RGBMatrix, RGBMatrixOptions

options = RGBMatrixOptions()
options.rows = 32
options.cols = 64
options.chain_length = 4  # Locked to 256 width layout
options.parallel = 1
options.disable_hardware_pulsing = 0
options.gpio_slowdown = 5
options.brightness = 100
options.pwm_bits = 10
options.hardware_mapping = "adafruit-hat-pwm"

WIDTH = options.cols * options.chain_length   # 256
HEIGHT = options.rows                         # 32
print(f"Resolving Canvas: {WIDTH}x{HEIGHT}")

# ---------------------------------------------------------------------------
# Physarum Tunables & Species Metrics
# ---------------------------------------------------------------------------
N_AGENTS = 3500           # Total agent count across the ecosystem
EVAPORATION = 0.94        # Trail decay multiplier per frame
DIFFUSION_BLUR = 0.15     # Spatial trail bleeding factor

# Horizontal Perturbation Tuning
DRIFT_AMP = 0.75          # Maximum pixel shift per frame (higher = more turbulent)
DRIFT_WAVE = 12.0         # Spatial wavelength across the height (Y) axis
DRIFT_SPEED = 2.6         # Velocity of the temporal wave phase shift

# Species Configuration Matrix
# Species 0: Fast, narrow-angle exploration (Cyan/Teal)
# Species 1: Slow, wide-angle branching networker (Magenta/Purple)
# Species 2: High-sensitivity, short-range scout (Gold/Amber)
SPECIES_CONFIG = {
    0: {"sensor_angle": 0.40, "sensor_dist": 6.0, "turn_angle": 0.35, "step_size": 1.1, "color": [0.0, 240.0, 210.0]},
    1: {"sensor_angle": 0.85, "sensor_dist": 4.5, "turn_angle": 0.55, "step_size": 0.7, "color": [245.0, 0.0, 160.0]},
    2: {"sensor_angle": 0.55, "sensor_dist": 8.0, "turn_angle": 0.25, "step_size": 1.4, "color": [255.0, 165.0, 0.0]}
}
NUM_SPECIES = len(SPECIES_CONFIG)

# ---------------------------------------------------------------------------
# Array Allocation Engine
# ---------------------------------------------------------------------------
# Agent Attributes: continuous X, continuous Y, continuous Heading Angle, Int Species
agent_x = np.random.uniform(0, WIDTH, N_AGENTS).astype(np.float32)
agent_y = np.random.uniform(0, HEIGHT, N_AGENTS).astype(np.float32)
agent_theta = np.random.uniform(0, 2.0 * math.pi, N_AGENTS).astype(np.float32)
agent_species = np.random.randint(0, NUM_SPECIES, N_AGENTS).astype(np.int32)

# Discrete Pheromone Map Array: shape (H, W, Channels)
trail_map = np.zeros((HEIGHT, WIDTH, NUM_SPECIES), dtype=np.float32)

# Compile species parameter maps into structural arrays for fast vector lookups
s_angle = np.array([SPECIES_CONFIG[s]["sensor_angle"] for s in range(NUM_SPECIES)], dtype=np.float32)[agent_species]
s_dist  = np.array([SPECIES_CONFIG[s]["sensor_dist"] for s in range(NUM_SPECIES)], dtype=np.float32)[agent_species]
s_turn  = np.array([SPECIES_CONFIG[s]["turn_angle"] for s in range(NUM_SPECIES)], dtype=np.float32)[agent_species]
s_step  = np.array([SPECIES_CONFIG[s]["step_size"] for s in range(NUM_SPECIES)], dtype=np.float32)[agent_species]

# Unpack color profile tensors
species_colors = np.array([SPECIES_CONFIG[s]["color"] for s in range(NUM_SPECIES)], dtype=np.float32)

rng = np.random.default_rng()

# ---------------------------------------------------------------------------
# Core Vectorized Processing Pipeline
# ---------------------------------------------------------------------------
def sense_field(ax, ay, angle_offset):
    """Samples the discrete chemical field at a specific offset angle ahead of the agents."""
    sample_angle = agent_theta + angle_offset
    sx = (ax + np.cos(sample_angle) * s_dist) % WIDTH
    sy = (ay + np.sin(sample_angle) * s_dist) % HEIGHT

    # Cast to integer array coordinates
    ix = sx.astype(np.int32) % WIDTH
    iy = sy.astype(np.int32) % HEIGHT

    # Vectorized indexing extraction across targeted channels
    return trail_map[iy, ix, agent_species]

def step_physarum():
    """Executes the concurrent perception, navigation, and trail deposition steps."""
    global agent_x, agent_y, agent_theta, trail_map

    # 1. Perception Phase (Sample Front, Left, and Right sensors)
    f_val = sense_field(agent_x, agent_y, 0.0)
    l_val = sense_field(agent_x, agent_y, s_angle)
    r_val = sense_field(agent_x, agent_y, -s_angle)

    # 2. Decision/Steering Phase
    # Default behavior: maintain heading. Modify based on chemical gradients
    steer = np.zeros(N_AGENTS, dtype=np.float32)

    # Condition: Left is strongest -> turn left
    steer = np.where((l_val > f_val) & (l_val > r_val), s_turn, steer)
    # Condition: Right is strongest -> turn right
    steer = np.where((r_val > f_val) & (r_val > l_val), -s_turn, steer)
    # Condition: Both flanks stronger than center -> randomly choose a direction
    random_choice = rng.choice([-1.0, 1.0], size=N_AGENTS).astype(np.float32)
    steer = np.where((l_val > f_val) & (np.abs(l_val - r_val) < 0.001), random_choice * s_turn, steer)

    # Inject micro-stochastic wander noise to prevent static lockups
    steer += rng.uniform(-0.08, 0.08, N_AGENTS)
    agent_theta = (agent_theta + steer) % (2.0 * math.pi)

    # 3. Locomotion Phase
    # Track the virtual timeline using the current state execution space
    t_phase = time() * DRIFT_SPEED

    # Calculate a unique spatial perturbation for each agent based on its Y position
    x_perturbation = DRIFT_AMP * np.sin((agent_y / DRIFT_WAVE) + t_phase)

    # Integrate headings and apply the continuous horizontal drift wave
    agent_x = (agent_x + np.cos(agent_theta) * s_step + x_perturbation) % WIDTH
    agent_y = (agent_y + np.sin(agent_theta) * s_step) % HEIGHT

    # 4. Chemical Trail Deposition Phase
    ix = agent_x.astype(np.int32) % WIDTH
    iy = agent_y.astype(np.int32) % HEIGHT

    # Accumulate agent footprints into matching species array indices
    np.add.at(trail_map, (iy, ix, agent_species), 1.0)

    # 5. Environment Field Evolution (Evaporation & Diffuse box pass)
    trail_map *= EVAPORATION

    # Wrap-around box blur over trail channels to simulate chemical diffusion
    diffused = 0.25 * (
        np.roll(trail_map, 1, axis=0) +
        np.roll(trail_map, -1, axis=0) +
        np.roll(trail_map, 1, axis=1) +
        np.roll(trail_map, -1, axis=1)
    )
    trail_map = trail_map * (1.0 - DIFFUSION_BLUR) + diffused * DIFFUSION_BLUR

def render_ecosystem():
    """Maps multi-channel chemical trails into high-contrast RGB space."""
    # Compress field concentrations down through an exposure saturation gate
    normalized = np.clip(trail_map * 1.8, 0.0, 1.0)

    # Matrix multiply channels by color profiles to generate the composite frame: (H, W, Chan) @ (Chan, 3) -> (H, W, 3)
    rgb = normalized @ species_colors

    # Soft gamma pass to pull faint filaments out from dark spaces
    rgb = np.clip((rgb / 255.0) ** 0.85 * 255.0, 0.0, 255.0)
    return rgb.astype(np.uint8)

# ---------------------------------------------------------------------------
# Main Control Cycle
# ---------------------------------------------------------------------------
matrix = RGBMatrix(options=options)
canvas = matrix.CreateFrameCanvas()

# Run a quick pre-warm loop iteration to lock in NumPy cache structures
step_physarum()
render_ecosystem()

target_dt = 1.0 / 60.0
print("Ecosystem initialized. Slime mold agents are online...\n")

while True:
    t0 = time()

    # Process physical updates
    step_physarum()

    # Render composite tracking canvas
    frame_bytes = render_buffer = render_ecosystem()
    image = Image.fromarray(frame_bytes, "RGB")

    canvas.SetImage(image)
    canvas = matrix.SwapOnVSync(canvas)

    # Enforce precise runtime pace regulation
    elapsed = time() - t0
    if elapsed < target_dt:
        sleep(target_dt - elapsed)
