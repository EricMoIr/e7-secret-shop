"""Bringing the app back to the front after the game had focus."""

import logging
import sys

from PySide6.QtWidgets import QWidget

log = logging.getLogger(__name__)


def bring_to_front(widget: QWidget) -> None:
    """Back to `widget`'s window after the game had focus (a setup capture, Read from game).
    Windows only lets the foreground app change focus, so this uses the same trick as focusing the game."""
    widget.raise_()
    widget.activateWindow()
    if sys.platform == "win32":
        try:
            from ..window import GameWindow

            GameWindow(int(widget.winId())).focus()
        except Exception:
            log.debug("Couldn't bring the window back to the front", exc_info=True)
