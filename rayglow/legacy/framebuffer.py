"""FeedbackBuffer: the float framebuffer that frame N+1 samples from frame N.

Buffer is float32 (H,W,3) in 0..1.  Gamma lives ONLY in composite() — gamma
inside the feedback loop would compound every frame and crush the image.
"""
import cv2
import numpy as np

from ..feed import config

BORDER = {
    "constant": cv2.BORDER_CONSTANT,   # trails fade off-edge to black (default)
    "wrap": cv2.BORDER_WRAP,           # seamless kaleidoscope
    "reflect": cv2.BORDER_REFLECT,
    "replicate": cv2.BORDER_REPLICATE,
}


class FeedbackBuffer:
    def __init__(self, width=config.WIDTH, height=config.HEIGHT):
        self.buf = np.zeros((height, width, 3), dtype=np.float32)

    def warp(self, map_x, map_y, border="constant"):
        """Resample the previous frame through the displacement maps."""
        self.buf = cv2.remap(self.buf, map_x, map_y, cv2.INTER_LINEAR,
                             borderMode=BORDER[border])

    def decay(self, d):
        if d != 1.0:
            self.buf *= d

    def clamp(self):
        """Clip to [0,1] after drawing — emulates MilkDrop's 8-bit feedback
        texture.  Without this, pixels under a repeatedly-drawn waveform
        equilibrate at brightness/(1-decay) >> 1 and the loop blows up."""
        np.clip(self.buf, 0.0, 1.0, out=self.buf)

    def composite(self, gamma=None, brightness=1.0):
        """Tone-map to uint8 for the matrix (out = (buf*brightness) ** gamma,
        the will-rpi-custom convention: <1 lifts faint trails, >1 deepens).
        brightness carries .milk fGammaAdj.  Leaves the float buf untouched
        so feedback continues from linear values."""
        if gamma is None:
            gamma = config.GAMMA   # resolved at call time so --gamma override works
        out = self.buf if brightness == 1.0 else self.buf * brightness
        out = np.clip(out, 0.0, 1.0) ** gamma
        return (out * 255.0 + 0.5).astype(np.uint8)

    def reset(self):
        self.buf[:] = 0.0
