"""What the shop logic needs from the game. `windows_game.WindowsGame` is the real implementation;
tests use a simulated shop."""

from typing import Protocol

from .geometry import Rect


class Game(Protocol):
    """All rectangles are relative to the game window's client area."""

    def focus(self) -> None: ...

    def client_size(self) -> tuple[int, int]: ...

    def read_text(self, region: Rect) -> str:
        """OCR text in the region, lower-cased."""
        ...

    def read_number(self, region: Rect) -> int | None:
        """OCR a number in the region, digits only. None if no digits were read."""
        ...

    def click(self, target: Rect) -> None:
        """Human-like click at a random point in `target`. Raises StopRun if the game loses focus."""
        ...

    def scroll(self, area: Rect, notches: int, notch_delay_ms: tuple[int, int]) -> None: ...

    def save_debug(self, region: Rect, label: str, note: str) -> None:
        """Save a capture of the region (and the note, e.g. the OCR text) for troubleshooting."""
        ...
