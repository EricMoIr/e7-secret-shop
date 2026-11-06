"""Starts the Qt application."""

import logging
import sys
import traceback
from types import TracebackType

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from .main_window import MainWindow

log = logging.getLogger("e7shop")

# How much larger than the system font the app's text is.
FONT_INCREASE_PX = 2


def enlarge_font(app: QApplication, px: int = FONT_INCREASE_PX) -> None:
    font = app.font()
    if font.pixelSize() > 0:
        font.setPixelSize(font.pixelSize() + px)
    else:
        # Points are 1/72 inch and Qt's logical pixels 1/96 inch, so 2 px is 1.5 pt.
        font.setPointSizeF(font.pointSizeF() + px * 72 / 96)
    app.setFont(font)


def _install_excepthook() -> None:
    """Show unexpected errors instead of dying silently: the packaged app has no console."""

    def hook(kind: type[BaseException], error: BaseException, tb: TracebackType | None) -> None:
        log.critical("Unexpected error", exc_info=(kind, error, tb))
        details = "".join(traceback.format_exception(kind, error, tb))
        try:
            box = QMessageBox(QMessageBox.Icon.Critical, "Unexpected error", f"{kind.__name__}: {error}")
            box.setDetailedText(details)
            box.exec()
        except Exception:
            sys.__excepthook__(kind, error, tb)

    sys.excepthook = hook


def run_gui(config_path: str | None = None) -> int:
    app = QApplication(sys.argv[:1])
    app.setApplicationName("E7 Secret Shop")
    _install_excepthook()
    enlarge_font(app)
    window = MainWindow(config_path)
    window.show()
    # Once the window is up: run setup if there's no config, or offer it if the config is invalid.
    QTimer.singleShot(0, window.offer_setup_if_needed)
    result = app.exec()
    # Destroy the window before the application, or Qt can crash on exit.
    del window
    return result
