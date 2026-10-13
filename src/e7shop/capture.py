"""Screenshots of screen regions."""

from PIL import Image

from .geometry import Rect


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
