"""The real `Game`: the Epic Seven window, screen capture, Tesseract and SendInput."""

import logging
import random
from datetime import datetime
from pathlib import Path

from PIL import Image

from .abort import AbortSignal
from .capture import ScreenCapture
from .config import Config
from .errors import StopKind, StopRun
from .geometry import Rect
from .mouse import Mouse
from .ocr import TesseractOcr, find_tesseract, parse_number
from .win_input import SendInputBackend
from .window import GameWindow

log = logging.getLogger(__name__)


class WindowsGame:
    def __init__(self, config: Config, abort: AbortSignal, *, dry_run: bool = False, rng: random.Random | None = None):
        tesseract = find_tesseract(config.tesseract_path)
        if tesseract is None:
            raise StopRun(
                StopKind.ERROR,
                "Tesseract OCR wasn't found"
                + (f" at {config.tesseract_path}" if config.tesseract_path else "")
                + ". Install it, or set tesseract_path in the config.",
            )
        log.debug("Tesseract: %s", tesseract)
        self._config = config
        self._dry_run = dry_run
        self._window = GameWindow.find(config.process_name)
        self._capture = ScreenCapture()
        self._ocr = TesseractOcr(
            str(tesseract),
            text_psm=config.ocr.text_psm,
            number_whitelist=config.ocr.number_whitelist,
            upscale=config.ocr.upscale,
        )
        self._mouse = Mouse(SendInputBackend(), abort, rng)
        self._debug_dir = Path(config.debug_dir)

    def focus(self) -> None:
        self._window.focus()

    def client_size(self) -> tuple[int, int]:
        return self._window.client_size()

    def grab(self, region: Rect) -> Image.Image:
        return self._capture.grab(self._to_screen(region))

    def read_text(self, region: Rect) -> str:
        return self._ocr.text(self.grab(region))

    def read_number(self, region: Rect) -> int | None:
        raw = self._ocr.number_raw(self.grab(region))
        log.debug("Number OCR %s: %r", list(region), raw)
        return parse_number(raw)

    def click(self, target: Rect) -> None:
        self._mouse.click_in(self._to_screen(target), before_press=self._ensure_foreground, press=not self._dry_run)

    def scroll(self, area: Rect, notches: int, notch_delay_ms: tuple[int, int]) -> None:
        # Scrolling spends nothing, so a dry run scrolls too; that keeps page 2 readable.
        self._mouse.scroll_in(self._to_screen(area), notches, notch_delay_ms, before_scroll=self._ensure_foreground)

    def save_debug(self, region: Rect, label: str, note: str) -> None:
        self._debug_dir.mkdir(parents=True, exist_ok=True)
        stem = self._debug_dir / f"{datetime.now():%Y%m%d-%H%M%S-%f}_{label}"
        try:
            self.grab(region).save(f"{stem}.png")
            self.grab(self._config.window_rect).save(f"{stem}_window.png")
        except Exception:
            log.exception("Couldn't save debug capture")
        Path(f"{stem}.txt").write_text(f"region: {list(region)}\n{note}\n", encoding="utf-8")
        log.info("Saved debug capture %s.png", stem)

    def inspect(self, out_dir: Path) -> list[tuple[str, str]]:
        """Capture the window and every configured region and OCR each one. For tuning the config."""
        out_dir.mkdir(parents=True, exist_ok=True)
        self.grab(self._config.window_rect).save(out_dir / "window.png")
        number_regions = {self._config.currency.gold_region, self._config.currency.skystones_region}
        results = []
        for label, rect in self._config.iter_rects():
            image = self.grab(rect)
            image.save(out_dir / f"{label}.png")
            text = f"number: {self._ocr.number_raw(image)!r}" if rect in number_regions else repr(self._ocr.text(image).strip())
            results.append((label, text))
        return results

    def _to_screen(self, region: Rect) -> Rect:
        """Config region → screen coordinates. Regions are measured at `window_size`; the game scales its
        contents with its window, so they're scaled to the window's current size first. The size is read
        every time, so a window resized during a run still works."""
        width, height = self._window.client_size()
        reference = self._config.window_size
        return self._window.to_screen(region.scaled(width / reference.width, height / reference.height))

    def close(self) -> None:
        self._capture.close()

    def _ensure_foreground(self) -> None:
        if not self._window.is_foreground():
            raise StopRun(StopKind.ERROR, "The game window lost focus; stopped so clicks don't land in another app")
