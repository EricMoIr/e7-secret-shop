"""What the setup wizard needs from the game: screenshots, one scroll, and OCR on crops (PLAN.md §9.3).

`WindowsSetupSession` is the real one. Tests use a fake with synthetic screenshots.
"""

import logging
import time
from typing import Protocol

from PIL import Image

from .abort import AbortSignal
from .capture import STILL_MAX_DIFF, ScreenCapture, mean_difference
from .config import OcrConfig
from .errors import StopKind, StopRun
from .geometry import Rect
from .ocr import TesseractOcr, parse_number

log = logging.getLogger(__name__)


class SetupSession(Protocol):
    def check_game(self) -> None:
        """Raise StopRun with a message for the user when the game isn't running."""
        ...

    def capture(self) -> Image.Image:
        """Focus the game and capture its whole client area. The image size is the client size."""
        ...

    def scroll_to_bottom(self, area: Rect, max_notches: int) -> int:
        """Scroll the list down one wheel notch at a time until it stops moving. Returns how many
        notches moved it (0: it never moved). `area` is relative to the client area."""
        ...

    def read_text(self, image: Image.Image) -> str:
        """OCR text, lower-cased."""
        ...

    def read_number(self, image: Image.Image) -> int | None:
        """OCR a number, digits only. None if no digits were read."""
        ...

    def close(self) -> None: ...


class WindowsSetupSession:
    def __init__(
        self,
        tesseract_path: str,
        *,
        process_name: str,
        ocr: OcrConfig,
        after_focus_ms: int,
        after_scroll_ms: int,
        settle_check_ms: int,
    ):
        self._process_name = process_name
        self._after_focus_s = after_focus_ms / 1000
        self._settle_timeout_s = max(after_scroll_ms, 1000) / 1000
        self._settle_check_s = settle_check_ms / 1000
        self._ocr = TesseractOcr(
            tesseract_path, text_psm=ocr.text_psm, number_whitelist=ocr.number_whitelist, upscale=ocr.upscale
        )
        self._capture = ScreenCapture()
        self._window = None

    def _game_window(self):
        from .window import GameWindow

        # Found again every time: the game may have been restarted while the wizard was open.
        self._window = GameWindow.find(self._process_name)
        return self._window

    def check_game(self) -> None:
        self._game_window()

    def capture(self) -> Image.Image:
        window = self._game_window()
        window.focus()
        time.sleep(self._after_focus_s)
        width, height = window.client_size()
        if width <= 0 or height <= 0:
            raise StopRun(StopKind.ERROR, "The game window is minimized. Restore it and try again.")
        return self._capture.grab(window.to_screen(Rect(0, 0, width, height)))

    def scroll_to_bottom(self, area: Rect, max_notches: int) -> int:
        from .mouse import WHEEL_DELTA, Mouse
        from .win_input import SendInputBackend

        window = self._game_window()
        window.focus()
        time.sleep(self._after_focus_s)
        backend = SendInputBackend()
        # Move into the list the way a run does, without turning the wheel yet.
        Mouse(backend, AbortSignal()).scroll_in(window.to_screen(area), 0, (0, 0))

        def look() -> Image.Image:
            if not window.is_foreground():
                raise StopRun(StopKind.ERROR, "The game window lost focus while scrolling")
            return self._capture.grab(window.to_screen(area))

        moved = 0
        before = look()
        for _ in range(max_notches):
            backend.wheel(-WHEEL_DELTA)
            after = self._settled(look)
            if mean_difference(before, after) < STILL_MAX_DIFF:
                break  # this notch did nothing: the list is at the bottom
            moved += 1
            before = after
        log.debug("The list reached the bottom after %d notch(es)", moved)
        return moved

    def _settled(self, look) -> Image.Image:
        """Look until two looks in a row match (the scroll animation ended), or the timeout."""
        previous = look()
        deadline = time.monotonic() + self._settle_timeout_s
        while time.monotonic() < deadline:
            time.sleep(self._settle_check_s)
            current = look()
            if mean_difference(previous, current) < STILL_MAX_DIFF:
                return current
            previous = current
        return previous

    def read_text(self, image: Image.Image) -> str:
        return self._ocr.text(image)

    def read_number(self, image: Image.Image) -> int | None:
        raw = self._ocr.number_raw(image)
        log.debug("Setup number OCR: %r", raw)
        return parse_number(raw)

    def close(self) -> None:
        self._capture.close()
