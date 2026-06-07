#!/usr/bin/env python3
"""Boids flocking visualization for a 320x32 HUB75 LED matrix.

Craig Reynolds' classic three rules (separation, alignment, cohesion) drive
~100 boids whose positions/velocities are tracked as floating-point vectors
(sub-pixel) and rendered one pixel each. Color encodes either the boid's
heading or its distance from the flock's center of mass; the mode cycles
periodically. The world wraps toroidally so the flock streams continuously
across the very wide, short display.
"""

import math
import random
import colorsys
from time import sleep

from rgbmatrix import RGBMatrix, RGBMatrixOptions


# ---------------------------------------------------------------------------
# Matrix hardware config (matches the rest of this rig — see CLAUDE.md / memory)
# ---------------------------------------------------------------------------
options = RGBMatrixOptions()
options.rows = 32
options.cols = 64
options.chain_length = 4
options.parallel = 1
options.disable_hardware_pulsing = 0       # snd_bcm2835 is blacklisted
options.gpio_slowdown = 5                   # tuned: 5 + lsb 130 ~eliminates panel-1 end-of-line artifact
options.brightness = 100
options.pwm_bits = 10
options.hardware_mapping = "adafruit-hat-pwm"  # GPIO4->GPIO18 jumper installed (hardware OE pulsing)
# options.pixel_mapper_config = "Rotate:180"  # disabled: panel physically flipped when 5th panel removed

WIDTH = options.cols * options.chain_length   # 320
HEIGHT = options.rows                         # 32
print(WIDTH, "x", HEIGHT)


# ---------------------------------------------------------------------------
# Flocking tunables
# ---------------------------------------------------------------------------
NUM_BOIDS = 100

PERCEPTION = 6.0          # neighbor radius (pixels) for alignment/cohesion
SEPARATION_DIST = 3.0     # boids closer than this push apart
PERCEPTION_SQ = PERCEPTION * PERCEPTION
SEPARATION_SQ = SEPARATION_DIST * SEPARATION_DIST

MAX_SPEED = 1.6      # pixels per tick
MAX_FORCE = 0.05          # steering accel cap per tick

W_SEPARATION = 1.6
W_ALIGNMENT = 1.0
W_COHESION = 0.9

MODE_TICKS = 1200         # frames between color-mode switches
NUM_MODES = 0             # 0 = color by heading, 1 = color by distance-to-center


def _limit(vx, vy, maximum):
    """Clamp a vector's magnitude to `maximum`, returning the (possibly) scaled vector."""
    mag_sq = vx * vx + vy * vy
    if mag_sq > maximum * maximum and mag_sq > 0.0:
        scale = maximum / math.sqrt(mag_sq)
        return vx * scale, vy * scale
    return vx, vy


def _wrap_delta(a, b, span):
    """Shortest signed distance from a to b on a torus of width `span`."""
    d = b - a
    if d > span * 0.5:
        d -= span
    elif d < -span * 0.5:
        d += span
    return d


class Boid:
    def __init__(self):
        self.x = random.uniform(0, WIDTH)
        self.y = random.uniform(0, HEIGHT)
        angle = random.uniform(0, 2 * math.pi)
        speed = random.uniform(0.5, MAX_SPEED)
        self.vx = math.cos(angle) * speed
        self.vy = math.sin(angle) * speed

    def flock(self, boids):
        # Accumulators for the three rules.
        sep_x = sep_y = 0.0
        ali_x = ali_y = 0.0
        coh_x = coh_y = 0.0
        sep_count = neigh_count = 0

        for other in boids:
            if other is self:
                continue
            dx = _wrap_delta(self.x, other.x, WIDTH)
            dy = _wrap_delta(self.y, other.y, HEIGHT)
            dist_sq = dx * dx + dy * dy
            if dist_sq > PERCEPTION_SQ or dist_sq == 0.0:
                continue

            # Alignment + cohesion use everyone in the perception radius.
            ali_x += other.vx
            ali_y += other.vy
            coh_x += dx          # vector toward the neighbor
            coh_y += dy
            neigh_count += 1

            # Separation: only very close neighbors, weighted by closeness.
            if dist_sq < SEPARATION_SQ:
                sep_x -= dx / dist_sq
                sep_y -= dy / dist_sq
                sep_count += 1

        ax = ay = 0.0

        if sep_count > 0:
            sx, sy = self._steer(sep_x, sep_y)
            ax += sx * W_SEPARATION
            ay += sy * W_SEPARATION

        if neigh_count > 0:
            # Alignment: match average heading.
            sx, sy = self._steer(ali_x / neigh_count, ali_y / neigh_count)
            ax += sx * W_ALIGNMENT
            ay += sy * W_ALIGNMENT
            # Cohesion: steer toward average neighbor position.
            sx, sy = self._steer(coh_x / neigh_count, coh_y / neigh_count)
            ax += sx * W_COHESION
            ay += sy * W_COHESION

        self.vx += ax
        self.vy += ay
        self.vx, self.vy = _limit(self.vx, self.vy, MAX_SPEED)

    def _steer(self, desired_x, desired_y):
        """Reynolds steering: desired velocity (at max speed) minus current, force-capped."""
        mag = math.sqrt(desired_x * desired_x + desired_y * desired_y)
        if mag == 0.0:
            return 0.0, 0.0
        desired_x = desired_x / mag * MAX_SPEED
        desired_y = desired_y / mag * MAX_SPEED
        return _limit(desired_x - self.vx, desired_y - self.vy, MAX_FORCE)

    def move(self):
        self.x = (self.x + self.vx) % WIDTH
        self.y = (self.y + self.vy) % HEIGHT


def heading_color(boid):
    """Hue from velocity direction; full saturation/value."""
    angle = math.atan2(boid.vy, boid.vx)        # -pi..pi
    hue = (angle + math.pi) / (2 * math.pi)     # 0..1
    r, g, b = colorsys.hsv_to_rgb(hue, 1.0, 1.0)
    return int(r * 255), int(g * 255), int(b * 255)


def distance_color(boid, cx, cy, max_dist):
    """Hue from distance to flock centroid: core warm (red), stragglers cool (blue)."""
    dx = _wrap_delta(boid.x, cx, WIDTH)
    dy = _wrap_delta(boid.y, cy, HEIGHT)
    dist = math.sqrt(dx * dx + dy * dy)
    t = min(dist / max_dist, 1.0) if max_dist > 0 else 0.0
    hue = t * 0.66                              # 0 (red) -> 0.66 (blue)
    r, g, b = colorsys.hsv_to_rgb(hue, 1.0, 1.0)
    return int(r * 255), int(g * 255), int(b * 255)


def flock_centroid(boids):
    """Centroid on a torus via mean of angles (avoids the wrap-around averaging bug)."""
    sx = sum(math.sin(b.x / WIDTH * 2 * math.pi) for b in boids)
    cx = sum(math.cos(b.x / WIDTH * 2 * math.pi) for b in boids)
    sy = sum(math.sin(b.y / HEIGHT * 2 * math.pi) for b in boids)
    cy = sum(math.cos(b.y / HEIGHT * 2 * math.pi) for b in boids)
    mx = (math.atan2(sx, cx) % (2 * math.pi)) / (2 * math.pi) * WIDTH
    my = (math.atan2(sy, cy) % (2 * math.pi)) / (2 * math.pi) * HEIGHT
    return mx, my


matrix = RGBMatrix(options=options)
canvas = matrix.CreateFrameCanvas()
print("Matrix initialized\n")

boids = [Boid() for _ in range(NUM_BOIDS)]

# Reference distance for the distance-color mode (~quarter of the short axis).
MAX_CENTER_DIST = HEIGHT * 0.75

mode = 0
mode_ticks = MODE_TICKS
while True:
    for b in boids:
        b.flock(boids)
    for b in boids:
        b.move()

    canvas.Clear()

    if mode == 1:
        cx, cy = flock_centroid(boids)

    for b in boids:
        if mode == 0:
            r, g, c = heading_color(b)
        else:
            r, g, c = distance_color(b, cx, cy, MAX_CENTER_DIST)
        canvas.SetPixel(int(b.x), int(b.y), r, g, c)

    canvas = matrix.SwapOnVSync(canvas)

    mode_ticks -= 1
    if mode_ticks < 0:
        mode_ticks = MODE_TICKS
        mode = (mode + 1) % NUM_MODES

    sleep(0.02)
