"""Playlist: rotate through .milk presets with auto-advance + keyboard control.

Keys (in the renderer terminal): n = next, p = previous, space = hold/resume,
r = random jump, q = quit.  Auto-advance every `duration` seconds unless held.
"""
import random
import select
import sys
import termios
import time
import tty
from pathlib import Path


class Keyboard:
    """Nonblocking single-key reads from the controlling terminal."""

    def __init__(self):
        self.enabled = sys.stdin.isatty()
        self._old = None
        if self.enabled:
            try:
                self._old = termios.tcgetattr(sys.stdin.fileno())
                tty.setcbreak(sys.stdin.fileno())
            except (termios.error, OSError):
                self.enabled = False

    def poll(self):
        if not self.enabled:
            return None
        if select.select([sys.stdin], [], [], 0)[0]:
            return sys.stdin.read(1)
        return None

    def restore(self):
        if self._old is not None:
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self._old)


class Playlist:
    def __init__(self, path, duration=20.0, shuffle=False):
        p = Path(path)
        if p.is_dir():
            self.files = sorted(p.glob("*.milk"))
        elif p.suffix == ".txt":          # triage keepers list: one path per line
            self.files = [Path(line.strip()) for line in p.read_text().splitlines()
                          if line.strip() and Path(line.strip()).exists()]
        else:
            self.files = [p]
        if not self.files:
            raise SystemExit(f"no .milk files found at {path}")
        if shuffle:
            random.shuffle(self.files)
        self.duration = duration
        self.idx = -1
        self.held = False
        self.started_at = 0.0
        self.skipped = []           # (name, reason)
        self._presets = None        # filled by preload()

    def preload(self):
        """Parse+compile every preset NOW.  Must happen before RGBMatrix()
        drops root: preset files under /home/will become unreadable after
        the privilege drop, so runtime file loads would fail."""
        from .dotmilk.runtime import MilkPreset
        self._presets = []
        for path in self.files:
            try:
                self._presets.append(MilkPreset(str(path)))
            except Exception as e:
                self._presets.append(None)
                self.skipped.append((path.name, str(e)))
        n_ok = sum(p is not None for p in self._presets)
        print(f"preloaded {n_ok}/{len(self.files)} presets"
              + (f" ({len(self.skipped)} skipped)" if self.skipped else ""))

    def _load(self, engine, now):
        """Advance to the next loadable preset starting at self.idx."""
        if self._presets is None:
            self.preload()
        for _ in range(len(self.files)):
            preset = self._presets[self.idx % len(self.files)]
            if preset is not None:
                engine.set_preset(preset)
                self.started_at = now
                print(f"[{self.idx % len(self.files) + 1}/{len(self.files)}] {preset.name}")
                return
            self.idx += 1
        raise SystemExit("every preset in the playlist failed to load")

    def advance(self, engine, now, step=1):
        self.idx = (self.idx + step) % len(self.files)
        self._load(engine, now)

    def tick(self, engine, now, key):
        """Call once per frame; handles keys + auto-advance.  Returns False to quit."""
        if self.idx < 0:
            self.idx = 0
            self._load(engine, now)
        if key == "q":
            return False
        if key == "n":
            self.advance(engine, now)
        elif key == "p":
            self.advance(engine, now, -1)
        elif key == "r":
            self.advance(engine, now, random.randrange(1, len(self.files)))
        elif key == " ":
            self.held = not self.held
            print("   [held]" if self.held else "   [rotating]")
        elif not self.held and now - self.started_at >= self.duration:
            self.advance(engine, now)
        # feed preset progress (the .milk `progress` variable)
        engine.features.progress = min(1.0, (now - self.started_at) / self.duration)
        return True
