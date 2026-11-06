"""Screenshots of screen regions."""

import numpy as np
from PIL import Image

from .geometry import Rect

# Two looks at a region whose mean grayscale difference is below this (out of 255) show the same thing.
STILL_MAX_DIFF = 1.0


def mean_difference(a: Image.Image, b: Image.Image) -> float:
    """Mean absolute grayscale difference between two same-sized images, 0-255. Different sizes count as 255."""
    if a.size != b.size:
        return 255.0
    x = np.asarray(a.convert("L"), dtype=np.int16)
    y = np.asarray(b.convert("L"), dtype=np.int16)
    return float(np.abs(x - y).mean())


class ScreenCapture:
    def __init__(self) -> None:
        import mss

        self._sct = mss.mss()

    def grab(self, rect: Rect) -> Image.Image:
        """Capture a rectangle given in screen coordinates."""
        shot = self._sct.grab({"left": rect.x0, "top": rect.y0, "width": rect.width, "height": rect.height})
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")

    def close(self) -> None:
        self._sct.close()
