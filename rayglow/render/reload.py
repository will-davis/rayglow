"""Hot reload: stat the .glsl every frame, recompile on mtime change.

Requires the process to keep root (options.drop_privileges = 0) — after the
default privilege drop, files under  become unreadable.
"""
import os


class GlslWatcher:
    def __init__(self, path):
        self.path = path
        self._mtime = os.stat(path).st_mtime

    def changed(self):
        """True once per file modification.  Missing file (editor save with
        delete+rename mid-flight) counts as unchanged — we'll see the new
        mtime on a later frame."""
        try:
            m = os.stat(self.path).st_mtime
        except OSError:
            return False
        if m != self._mtime:
            self._mtime = m
            return True
        return False

    def read(self):
        with open(self.path) as f:
            return f.read()
