"""Starts the Qt application."""

import sys

from PySide6.QtWidgets import QApplication

from .main_window import MainWindow


def run_gui(config_path: str | None = None) -> int:
    app = QApplication(sys.argv[:1])
    app.setApplicationName("E7 Secret Shop")
    window = MainWindow(config_path)
    window.show()
    result = app.exec()
    # Destroy the window before the application, or Qt can crash on exit.
    del window
    return result
