"""Milk-Pi: MilkDrop-style audio-reactive feedback renderer for the LED matrix.

Pi-side half of the desktop->Pi UDP feature-streaming system described in
MilkDrop3/project-milk-pi.md.  Engine (this package) is preset-agnostic;
presets (milk/presets/) are pure functions FeatureState -> steering dict,
mirroring MilkDrop's host/preset split.

Run:
  headless benchmark (no root):  ~/rgbvenv/bin/python -m milk --headless --preset tunnel
  hardware:                      sudo ~/rgbvenv/bin/python -m milk --preset tunnel
  fake feature source:           ~/rgbvenv/bin/python milk/fake_sender.py
"""
