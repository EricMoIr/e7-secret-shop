"""`--self-test FILE`: check that a build bundles everything, without the game or admin rights.

It imports every module the app loads lazily, builds the main window and the setup wizard with Qt's
real Windows platform plugin (without showing them), grabs a few screen pixels, and reads rendered
text and a price with the bundled Tesseract. It writes "ok" or the error to FILE and
exits 0 or 1. The packaging script runs it on a console build of the same bundle.
"""

import importlib
import tempfile
import traceback
from pathlib import Path

MODULES = [
    "numpy",
    "PIL.Image",
    "pydantic",
    "pytesseract",
    "mss",
    "psutil",
    "keyboard",
    "win32api",
    "win32con",
    "win32gui",
    "win32process",
    "PySide6.QtWidgets",
    "e7shop.calibrate",
    "e7shop.cli",
    "e7shop.elevation",
    "e7shop.gui.main_window",
    "e7shop.gui.setup_wizard",
    "e7shop.setup_session",
    "e7shop.win_input",
    "e7shop.window",
    "e7shop.windows_game",
]


def _check() -> list[str]:
    lines = []
    for name in MODULES:
        importlib.import_module(name)
    lines.append(f"imported {len(MODULES)} modules")

    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication

    from .capture import ScreenCapture
    from .geometry import Rect
    from .gui.main_window import MainWindow
    from .gui.setup_wizard import SetupWizard

    app = QApplication.instance() or QApplication([])
    lines.append(f"Qt platform: {app.platformName()}")
    with tempfile.TemporaryDirectory() as tmp:
        settings = QSettings(str(Path(tmp) / "settings.ini"), QSettings.Format.IniFormat)
        window = MainWindow(str(Path(tmp) / "config.json"), register_hotkey=lambda _key: (lambda: None), settings=settings)
        wizard = SetupWizard(Path(tmp) / "config.json", None, parent=window)
        lines.append(f"windows built: {window.windowTitle()!r}, {wizard.windowTitle()!r}")
        wizard.deleteLater()
        window.deleteLater()
        app.processEvents()

    capture = ScreenCapture()
    image = capture.grab(Rect(0, 0, 8, 8))
    capture.close()
    lines.append(f"screen capture: {image.size}")
    lines.append(_check_ocr())
    return lines


def _render(text: str):
    from PIL import Image, ImageDraw, ImageFont

    font = ImageFont.load_default(size=64)
    left, top, right, bottom = font.getbbox(text)
    image = Image.new("RGB", (right - left + 80, bottom - top + 80), "white")
    ImageDraw.Draw(image).text((40 - left, 40 - top), text, fill="black", font=font)
    return image


def _check_ocr() -> str:
    from .ocr import TesseractOcr, bundled_tesseract

    exe = bundled_tesseract()
    if exe is None:
        raise RuntimeError("no bundled Tesseract")
    ocr = TesseractOcr(str(exe))
    price = ocr.number(_render("184,000"))
    if price != 184_000:
        raise RuntimeError(f"bundled Tesseract read the price 184,000 as {price!r}")
    # This checks that the bundled Tesseract loads its DLLs and language data, not OCR accuracy on
    # the game's fonts (setup checks that on real screenshots). A sentence reads more reliably than
    # a lone short title: Pillow's default font gives "Secret Shop" alone as "Secret Shoo".
    text = ocr.text(_render("Refresh the shop? Confirm"))
    if "confirm" not in text or "refresh" not in text:
        raise RuntimeError(f"bundled Tesseract read 'Refresh the shop? Confirm' as {text!r}")
    return f"bundled Tesseract at {exe} read 184,000 and 'Refresh the shop? Confirm'"


def run_self_test(out_file: str) -> int:
    try:
        lines = _check()
    except BaseException:
        Path(out_file).write_text("FAILED\n" + traceback.format_exc(), encoding="utf-8")
        return 1
    Path(out_file).write_text("ok\n" + "\n".join(lines) + "\n", encoding="utf-8")
    return 0
