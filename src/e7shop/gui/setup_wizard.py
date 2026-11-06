"""First-run setup wizard (PLAN.md §9): the user marks regions on screenshots of the game, each one is
checked with OCR, and the result is written as the config file."""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image
from pydantic import ValidationError
from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QPixmap, QShowEvent
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
    QWizard,
    QWizardPage,
)

from ..calibrate import (
    MATCH_THRESHOLD,
    MAX_SCROLL_NOTCHES,
    SCROLL_NOTCHES,
    DetectionError,
    Dialog,
    Measured,
    Row,
    build_config,
    find_rows,
    region_changed,
    rows_above,
    save_config,
    scores_at,
    run_notches,
    scroll_area,
    wanted_items_from,
)
from ..config import Config, OcrConfig, TimingConfig
from ..errors import StopRun
from ..geometry import Rect
from ..ocr import STANDARD_INSTALL, bundled_tesseract
from ..setup_session import SetupSession
from .focus import bring_to_front
from .region_picker import MarkingPanel, RegionSpec, to_qimage

log = logging.getLogger(__name__)

CAPTURE, SCREEN, ITEMS1, ITEMS2, BUY, REFRESH, FINISH = range(7)
PAGE1_ROWS = 4  # the shop shows 4 items before scrolling
PAGE2_ROWS = 2  # and 2 more at the bottom

SessionFactory = Callable[[str, dict[str, Any]], SetupSession]
MessageFn = Callable[[QMessageBox.Icon, str, str], None]

RESIZE_WARNING = (
    "<b>Set the game window to the size you'll play at before continuing, and don't resize it afterwards.</b> "
    "Everything you mark is tied to this exact size. Moving the window is fine. If you resize it later, "
    "runs will refuse to start until you run setup again."
)


def _valid(model: type, value: Any) -> Any:
    try:
        return model.model_validate(value if isinstance(value, dict) else {})
    except ValidationError:
        return model()


def windows_session_factory(tesseract_path: str, base: dict[str, Any]) -> SetupSession:
    from ..setup_session import WindowsSetupSession

    timings = _valid(TimingConfig, base.get("timings"))
    return WindowsSetupSession(
        tesseract_path,
        process_name=str(base.get("process_name") or Config.model_fields["process_name"].default),
        ocr=_valid(OcrConfig, base.get("ocr")),
        after_focus_ms=timings.after_focus_ms,
        after_scroll_ms=timings.after_scroll_ms,
        settle_check_ms=timings.settle_check_ms,
    )


@dataclass
class SetupState:
    size: tuple[int, int] | None = None
    shop: Image.Image | None = None
    page2: Image.Image | None = None
    buy: Image.Image | None = None
    refresh: Image.Image | None = None
    page1_rows: list[Row] = field(default_factory=list)
    pitch: float | None = None
    page2_rows: list[Row] = field(default_factory=list)
    # Wheel notches it took to reach the bottom of the list.
    notches_to_bottom: int | None = None


class SetupWizard(QWizard):
    def __init__(
        self,
        config_path: str | Path,
        base: dict[str, Any] | None = None,
        *,
        session_factory: SessionFactory = windows_session_factory,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("E7 Secret Shop setup")
        self.setWizardStyle(QWizard.WizardStyle.ClassicStyle)
        self.setOption(QWizard.WizardOption.NoBackButtonOnStartPage)
        self.resize(1280, 820)

        self.config_path = Path(config_path).resolve()
        self.base: dict[str, Any] = base if isinstance(base, dict) else {}
        self.ocr = _valid(OcrConfig, self.base.get("ocr"))
        self.wanted = [(i["keyword"], i["name"]) for i in wanted_items_from(self.base.get("wanted_items"))]
        self.state = SetupState()
        self.session: SetupSession | None = None
        # The Tesseract shipped with the app. When there is one, setup doesn't ask for a path.
        self.bundled_tesseract = bundled_tesseract()
        # What goes in the config: None for the bundled one, or the path the user chose.
        self.tesseract_path: str | None = None
        self.saved_path: Path | None = None
        self._session_factory = session_factory
        debug_dir = Path(str(self.base.get("debug_dir") or "debug"))
        if not debug_dir.is_absolute():
            debug_dir = self.config_path.parent / debug_dir
        self._debug_dir = debug_dir / f"setup-{datetime.now():%Y%m%d-%H%M%S}"
        # Replaceable in tests: no modal boxes, no waiting, no Win32 focus calls.
        self.show_message: MessageFn = self._show_message_box
        self.hide_delay_s = 0.15
        self.bring_back: Callable[[QWidget], None] = bring_to_front

        self.setPage(CAPTURE, CapturePage())
        self.setPage(SCREEN, ScreenPage())
        self.setPage(ITEMS1, Page1ItemsPage())
        self.setPage(ITEMS2, Page2ItemsPage())
        self.setPage(BUY, DialogPage("buy"))
        self.setPage(REFRESH, DialogPage("refresh"))
        self.setPage(FINISH, FinishPage())
        self.setButtonText(QWizard.WizardButton.FinishButton, "Save")

    # Qt's Aero back arrow

    def _aero_back_buttons(self) -> list[QAbstractButton]:
        """Qt on Windows creates an Aero-style back arrow (QVistaBackButton, which has no class name of
        its own) in the wizard's top-left corner, and the Windows 11 theme shows it even in the classic
        style. The classic Back button at the bottom already does that job."""
        return [b for b in self.findChildren(QAbstractButton) if b.metaObject().className() == "QAbstractButton"]

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        for button in self._aero_back_buttons():
            button.installEventFilter(self)
            button.hide()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Show and watched in self._aero_back_buttons():
            watched.hide()
        return super().eventFilter(watched, event)

    # Session

    def open_session(self, tesseract_path: str, *, save_path: bool = True) -> bool:
        """Start talking to the game. `save_path` False keeps the path out of the config (bundled Tesseract)."""
        self.close_session()
        try:
            session = self._session_factory(tesseract_path, self.base)
            session.check_game()
        except StopRun as stop:
            self.show_message(QMessageBox.Icon.Warning, "Epic Seven", stop.reason)
            return False
        except Exception as exc:
            log.exception("Couldn't start setup")
            self.show_message(QMessageBox.Icon.Warning, "Couldn't start setup", str(exc))
            return False
        self.session = session
        self.tesseract_path = tesseract_path if save_path else None
        return True

    def close_session(self) -> None:
        if self.session is not None:
            self.session.close()
            self.session = None

    def done(self, result: int) -> None:
        # QWizard.done validates the last page first and stays open when that fails (e.g. saving failed).
        super().done(result)
        if not self.isVisible():
            self.close_session()

    # Capturing

    def _out_of_the_way(self, action: Callable[[], Any]) -> Any:
        """Run `action` with the wizard and its parent window invisible, so they can't end up in a
        screenshot or catch the scroll. Focusing the game also puts it on top of them."""
        windows = [self] + ([self.parentWidget().window()] if self.parentWidget() else [])
        for w in windows:
            w.setWindowOpacity(0.0)
        QApplication.processEvents()
        time.sleep(self.hide_delay_s)
        try:
            return action()
        finally:
            for w in windows:
                w.setWindowOpacity(1.0)
            self.bring_back(self)

    def capture(self, name: str, *, new_size: bool = False) -> Image.Image | None:
        """Take a screenshot of the game. Returns None, after telling the user, when it fails or the
        window size changed since the first capture (which restarts setup from the shop capture)."""
        assert self.session is not None
        try:
            image = self._out_of_the_way(self.session.capture)
        except StopRun as stop:
            self.show_message(QMessageBox.Icon.Warning, "Couldn't capture the game", stop.reason)
            return None
        except Exception as exc:
            log.exception("Capture failed")
            self.show_message(QMessageBox.Icon.Warning, "Couldn't capture the game", str(exc))
            return None
        if not new_size and not self._same_size(image):
            return None
        self._save_debug(image, name)
        return image

    def scroll_and_capture(self) -> Image.Image | None:
        """Scroll the list to the bottom one notch at a time, counting the notches, then capture page 2."""
        assert self.session is not None and self.state.page1_rows
        area = scroll_area(self.state.page1_rows)
        try:
            notches = self._out_of_the_way(lambda: self.session.scroll_to_bottom(area, MAX_SCROLL_NOTCHES))
        except StopRun as stop:
            self.show_message(QMessageBox.Icon.Warning, "Couldn't scroll the shop", stop.reason)
            return None
        if notches == 0:
            self.show_message(
                QMessageBox.Icon.Warning,
                "The list didn't move",
                "Scrolling didn't move the shop list. Make sure the Secret Shop is open with the list at "
                "the top and nothing covers it, then try again.",
            )
            return None
        log.info("The list reached the bottom after %d notch(es); runs will send %d", notches, -run_notches(notches))
        self.state.notches_to_bottom = notches
        return self.capture("page2")

    def _same_size(self, image: Image.Image) -> bool:
        expected = self.state.size
        if expected is None or image.size == expected:
            return True
        self.show_message(
            QMessageBox.Icon.Warning,
            "The game window changed size",
            f"The game window changed from {expected[0]}×{expected[1]} to {image.width}×{image.height}. "
            "Setup has to start again from the shop capture.",
        )
        self.restart_from_capture()
        return False

    def restart_from_capture(self) -> None:
        self.state = SetupState()
        for page_id in (SCREEN, ITEMS1, ITEMS2, BUY, REFRESH):
            self.page(page_id).reset()
        while self.currentId() > CAPTURE:
            self.back()
        self.page(CAPTURE).show_capture()

    def _save_debug(self, image: Image.Image, name: str) -> None:
        try:
            self._debug_dir.mkdir(parents=True, exist_ok=True)
            image.save(self._debug_dir / f"{name}.png")
        except Exception:
            log.warning("Couldn't save the setup screenshot %s", name, exc_info=True)

    # Checks shared by the pages

    def read_number(self, image: Image.Image) -> int | None:
        assert self.session is not None
        return self.session.read_number(image)

    def read_name(self, image: Image.Image) -> str:
        """An item name as the run reads it: lower-cased, whitespace collapsed."""
        assert self.session is not None
        return " ".join(self.session.read_text(image).split())

    def text_check(self, keyword: str) -> Callable[[Image.Image], tuple[bool, str]]:
        def check(image: Image.Image) -> tuple[bool, str]:
            assert self.session is not None
            text = " ".join(self.session.read_text(image).split())
            shown = f"“{text[:50]}”" if text else "no text read"
            return (keyword in text, shown if keyword in text else f"{shown}, expected “{keyword}”")

        return check

    def number_check(self, image: Image.Image) -> tuple[bool, str]:
        value = self.read_number(image)
        return (value is not None, f"{value:,}" if value is not None else "no number read")

    def name_check(self, image: Image.Image) -> tuple[bool, str]:
        text = self.read_name(image)
        return (bool(text), self.describe_name(text))

    def describe_name(self, text: str) -> str:
        if not text:
            return "no name read"
        wanted = next((name for keyword, name in self.wanted if keyword in text), None)
        return f"“{text}” → {wanted}" if wanted else f"“{text}”"

    # Saving

    def measured(self) -> Measured:
        screen: ScreenPage = self.page(SCREEN)
        buy: DialogPage = self.page(BUY)
        refresh: DialogPage = self.page(REFRESH)
        assert self.state.size is not None
        return Measured(
            window_size=self.state.size,
            secret_shop_region=screen.panel.rect("title"),
            gold_region=screen.panel.rect("gold"),
            skystones_region=screen.panel.rect("skystones"),
            refresh_button=screen.panel.rect("refresh"),
            page1=self.state.page1_rows,
            page2=self.state.page2_rows,
            buy_dialog=Dialog(buy.panel.rect("text"), buy.panel.rect("button")),
            refresh_dialog=Dialog(refresh.panel.rect("text"), refresh.panel.rect("button")),
            tesseract_path=self.tesseract_path,
            scroll_notches=run_notches(self.state.notches_to_bottom) if self.state.notches_to_bottom else SCROLL_NOTCHES,
        )

    def save(self) -> bool:
        try:
            data = build_config(self.measured(), self.base)
            backup = save_config(self.config_path, data)
        except (OSError, ValueError) as exc:
            log.exception("Couldn't save the config")
            self.show_message(QMessageBox.Icon.Critical, "Couldn't save the config", str(exc))
            return False
        log.info("Setup saved %s%s", self.config_path, f" (previous config kept as {backup.name})" if backup else "")
        self.saved_path = self.config_path
        return True

    def _show_message_box(self, icon: QMessageBox.Icon, title: str, text: str) -> None:
        QMessageBox(icon, title, text, QMessageBox.StandardButton.Ok, self).exec()


def _wrapped(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setTextFormat(Qt.TextFormat.RichText)
    return label


def _steps(*steps: str, after: str = "") -> QLabel:
    """A page's instructions as a numbered list, with an optional note under it."""
    items = "".join(f"<li>{step}</li>" for step in steps)
    return _wrapped(f"<ol style='margin-left: 0px; -qt-list-indent: 1;'>{items}</ol>{after}")


MARKING_NOTE = (
    "Each box is checked as soon as you draw it, and the list on the right moves on to the next one. "
    "To redo a box, click it in the list and drag again."
)


class _Page(QWizardPage):
    def wizard(self) -> SetupWizard:  # type: ignore[override]
        return super().wizard()  # type: ignore[return-value]

    @property
    def state(self) -> SetupState:
        return self.wizard().state

    def reset(self) -> None:
        """Forget everything marked on this page (the screenshots are being retaken)."""


class CapturePage(_Page):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Capture the Secret Shop")
        self.capture_button = QPushButton("Capture")
        self.capture_button.clicked.connect(self.capture)
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumHeight(360)
        self.size_label = QLabel()

        # Only shown when the app has no bundled Tesseract (running from source without vendor/).
        self.path_edit = QLineEdit()
        # A different Tesseract needs a new session.
        self.path_edit.textChanged.connect(lambda _text: self.wizard() and self.wizard().close_session())
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        path_row = QHBoxLayout()
        path_row.addWidget(QLabel("Tesseract OCR (<code>tesseract.exe</code>):"))
        path_row.addWidget(self.path_edit, 1)
        path_row.addWidget(browse)
        self.path_box = QWidget()
        self.path_box.setLayout(path_row)
        path_row.setContentsMargins(0, 0, 0, 0)

        layout = QVBoxLayout(self)
        layout.addWidget(
            _steps(
                "Start Epic Seven and set its window to the size you'll play at.",
                "Open the <b>Secret Shop</b>, with the item list scrolled to the top.",
                "Click <b>Capture</b>.",
                "Check the screenshot below, then click <b>Next</b>.",
            )
        )
        layout.addWidget(self.path_box)
        row = QHBoxLayout()
        row.addWidget(self.capture_button)
        row.addWidget(self.size_label, 1)
        layout.addLayout(row)
        layout.addWidget(self.preview, 1)

    def initializePage(self) -> None:
        wizard = self.wizard()
        self.path_box.setVisible(wizard.bundled_tesseract is None)
        if not self.path_edit.text():
            self.path_edit.setText(str(wizard.base.get("tesseract_path") or STANDARD_INSTALL))

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Find tesseract.exe", self.path_edit.text(), "tesseract.exe (*.exe)")
        if path:
            self.path_edit.setText(path)

    def _open_session(self) -> bool:
        wizard = self.wizard()
        if wizard.bundled_tesseract:
            return wizard.open_session(str(wizard.bundled_tesseract), save_path=False)
        path = self.path_edit.text().strip()
        if not Path(path).is_file():
            wizard.show_message(
                QMessageBox.Icon.Warning,
                "Tesseract not found",
                f"There's no tesseract.exe at {path}. Install Tesseract OCR, or browse to tesseract.exe.",
            )
            return False
        return wizard.open_session(path)

    def capture(self) -> None:
        if self.wizard().session is None and not self._open_session():
            return
        image = self.wizard().capture("shop", new_size=True)
        if image is None:
            return
        wizard = self.wizard()
        wizard.state = SetupState(size=image.size, shop=image)
        for page_id in (SCREEN, ITEMS1, ITEMS2, BUY, REFRESH):
            wizard.page(page_id).reset()
        self.show_capture()
        self.completeChanged.emit()

    def show_capture(self) -> None:
        image = self.state.shop
        if image is None:
            self.preview.clear()
            self.size_label.setText("")
            return
        pixmap = QPixmap.fromImage(to_qimage(image))
        self.preview.setPixmap(pixmap.scaled(720, 405, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        self.size_label.setText(f"Game window: {image.width}×{image.height}. This size will be saved with the setup.")

    def isComplete(self) -> bool:
        return self.state.shop is not None


class ScreenPage(_Page):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Mark the shop screen")
        self.panel = MarkingPanel()
        self.panel.changed.connect(self.completeChanged)
        layout = QVBoxLayout(self)
        layout.addWidget(
            _steps(
                "Drag a box around the “Secret Shop” title.",
                "Drag a tight box around just the digits of your gold. Leave out the coin icon: its “+” "
                "can be read as an extra digit.",
                "Drag a tight box around just the digits of your skystones, again without the icon.",
                "Drag a box around the shop's <b>Refresh</b> button.",
                after=MARKING_NOTE,
            )
        )
        layout.addWidget(self.panel, 1)

    def initializePage(self) -> None:
        wizard = self.wizard()
        self.panel.load(
            self.state.shop,
            [
                RegionSpec("title", "“Secret Shop” title", wizard.text_check(wizard.ocr.secret_shop_text)),
                RegionSpec("gold", "Gold", wizard.number_check),
                RegionSpec("skystones", "Skystones", wizard.number_check),
                RegionSpec("refresh", "Refresh button"),
            ],
        )

    def reset(self) -> None:
        self.panel.clear()

    def isComplete(self) -> bool:
        return self.panel.all_passed()


class _ItemsPage(_Page):
    """Shared by both item pages: mark one row, work out the others, and show them."""

    def __init__(self) -> None:
        super().__init__()
        self.panel = MarkingPanel()
        self.panel.changed.connect(self.update_rows)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.rows: list[Row] = []
        self.names: list[str] = []

    def _name_spec(self, key: str, label: str) -> RegionSpec:
        return RegionSpec(key, label, self.wizard().name_check)

    def compute_rows(self) -> list[Row]:
        """The rows from what's marked. Raises DetectionError (shown to the user) when they can't be found."""
        raise NotImplementedError

    def image(self) -> Image.Image | None:
        raise NotImplementedError

    def update_rows(self) -> None:
        self.rows, self.names = [], []
        image = self.image()
        message = ""
        if image is not None:
            try:
                self.rows = self.compute_rows()
            except DetectionError as exc:
                message = f"<b>Couldn't find the rows:</b> {exc}. {self.fallback_hint()}"
        if self.rows:
            assert image is not None
            wizard = self.wizard()
            self.names = [wizard.read_name(image.crop(tuple(r.name_region))) for r in self.rows]
            lines = [f"Row {i}: {wizard.describe_name(n)}" for i, n in enumerate(self.names, start=1)]
            unread = [str(i) for i, n in enumerate(self.names, start=1) if not n]
            if unread:
                lines.append(f"<b>No name read for row(s) {', '.join(unread)}.</b> Adjust the name box.")
            if note := self.rows_note():
                lines.append(f"<b>{note}</b>")
            message = "<br>".join(lines)
        self.status.setText(message)
        self.panel.set_overlays(
            [(r.name_region, "") for r in self.rows] + [(r.buy_button, f"Row {i}") for i, r in enumerate(self.rows, start=1)]
        )
        self.store()
        self.completeChanged.emit()

    def fallback_hint(self) -> str:
        return ""

    def rows_note(self) -> str:
        return ""

    def store(self) -> None:
        raise NotImplementedError

    def isComplete(self) -> bool:
        return bool(self.rows) and all(self.names)

    @staticmethod
    def _row(panel: MarkingPanel, name_key: str, button_key: str) -> Row | None:
        name, button = panel.rect(name_key), panel.rect(button_key)
        return Row(name, button) if name and button else None


class Page1ItemsPage(_ItemsPage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Mark one item (top of the list)")
        self.pitch: float | None = None
        layout = QVBoxLayout(self)
        layout.addWidget(
            _steps(
                "Drag a box around the name of any one of the 4 items (for example “Army Rescue Dog”).",
                "Drag a box around the same item's buy button.",
                "Setup finds the other items, since they all share the same layout, and outlines them in green "
                "with the name it reads for each. Check them, then click <b>Next</b>.",
            )
        )
        layout.addWidget(self.panel, 1)
        layout.addWidget(self.status)

    def initializePage(self) -> None:
        self.panel.load(self.state.shop, [self._name_spec("name", "Item name"), RegionSpec("button", "Buy button")])

    def image(self) -> Image.Image | None:
        return self.state.shop

    def compute_rows(self) -> list[Row]:
        self.pitch = None
        marked = self._row(self.panel, "name", "button")
        if not marked:
            return []
        assert self.state.shop is not None
        rows, self.pitch = find_rows(self.state.shop, marked, PAGE1_ROWS)
        return rows

    def fallback_hint(self) -> str:
        return "Redraw the buy button box tightly around the button."

    def store(self) -> None:
        self.state.page1_rows = list(self.rows)
        self.state.pitch = self.pitch if self.rows else None

    def reset(self) -> None:
        self.panel.clear()
        self.rows, self.names, self.pitch = [], [], None
        self.status.setText("")


class Page2ItemsPage(_ItemsPage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Mark the bottom item (after scrolling)")
        self.scroll_button = QPushButton("Scroll and capture")
        self.scroll_button.clicked.connect(self.scroll_and_capture)
        layout = QVBoxLayout(self)
        layout.addWidget(
            _steps(
                "Click <b>Scroll and capture</b>. Setup scrolls the shop list to the bottom, the same way a run "
                "does. Don't touch the mouse while it scrolls.",
                "Drag a box around the name of the <b>bottom</b> item.",
                "Drag a box around its buy button.",
                "Check the green outlines of the two new items, then click <b>Next</b>.",
            )
        )
        layout.addWidget(self.scroll_button)
        layout.addWidget(self.panel, 1)
        layout.addWidget(self.status)

    def initializePage(self) -> None:
        self.panel.load(
            self.state.page2,
            [self._name_spec("name", "Bottom item: name"), RegionSpec("button", "Bottom item: buy button")],
        )

    def scroll_and_capture(self) -> None:
        image = self.wizard().scroll_and_capture()
        if image is None:
            return
        assert self.state.shop is not None
        if not region_changed(self.state.shop, image, scroll_area(self.state.page1_rows)):
            self.wizard().show_message(
                QMessageBox.Icon.Warning,
                "The list didn't move",
                "The shop list looks the same as before scrolling. Make sure the Secret Shop is open "
                "and nothing covers it, then try again.",
            )
            return
        self.state.page2 = image
        self.panel.set_image(image)

    def image(self) -> Image.Image | None:
        return self.state.page2

    def compute_rows(self) -> list[Row]:
        bottom = self._row(self.panel, "name", "button")
        if not bottom or self.state.pitch is None:
            return []
        return rows_above(bottom, self.state.pitch, PAGE2_ROWS)

    def rows_note(self) -> str:
        # Cross-check: the page-1 button should match at both new rows.
        shop, page2 = self.state.shop, self.state.page2
        if not (shop and page2 and self.state.page1_rows and self.rows):
            return ""
        button = self.state.page1_rows[0].buy_button
        scores = scores_at(page2, shop.crop(tuple(button)), button.x0, self.rows)
        if min(scores) < MATCH_THRESHOLD:
            return "The green rows don't look like the rows on the first page. Check that they're right."
        return ""

    def store(self) -> None:
        self.state.page2_rows = list(self.rows)

    def reset(self) -> None:
        self.panel.clear()
        self.panel.set_image(None)
        self.rows, self.names = [], []
        self.status.setText("")


class DialogPage(_Page):
    TEXT = {
        "buy": ("Capture the buy dialog", "click the buy button of any item, so its purchase dialog opens"),
        "refresh": ("Capture the refresh dialog", "click the shop's <b>Refresh</b> button, so its confirmation dialog opens"),
    }

    def __init__(self, kind: str) -> None:
        super().__init__()
        self.kind = kind
        title, open_it = self.TEXT[kind]
        self.setTitle(title)
        self.capture_button = QPushButton("Capture")
        self.capture_button.clicked.connect(self.capture)
        self.panel = MarkingPanel()
        self.panel.changed.connect(self.completeChanged)
        layout = QVBoxLayout(self)
        layout.addWidget(
            _steps(
                f"In the game, {open_it}.",
                "Click <b>Capture</b>.",
                "Drag a box around the dialog's text.",
                "Drag a box around the dialog's confirm button.",
                "<b>Don't confirm:</b> press <b>Cancel</b> in the game, then click <b>Next</b>.",
            )
        )
        layout.addWidget(self.capture_button, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.panel, 1)

    @property
    def keyword(self) -> str:
        ocr = self.wizard().ocr
        return ocr.buy_text if self.kind == "buy" else ocr.confirm_text

    def initializePage(self) -> None:
        label = f"Dialog text (must contain “{self.keyword}”)"
        self.panel.load(
            getattr(self.state, self.kind),
            [RegionSpec("text", label, self.wizard().text_check(self.keyword)), RegionSpec("button", "Confirm button")],
        )

    def capture(self) -> None:
        image = self.wizard().capture(f"{self.kind}_dialog")
        if image is None:
            return
        setattr(self.state, self.kind, image)
        self.panel.set_image(image)

    def reset(self) -> None:
        self.panel.clear()
        self.panel.set_image(None)

    def isComplete(self) -> bool:
        return getattr(self.state, self.kind) is not None and self.panel.all_passed()


class FinishPage(_Page):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Save the setup")
        self.summary = _wrapped("")
        self.summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout = QVBoxLayout(self)
        layout.addWidget(self.summary)
        layout.addStretch(1)
        layout.addWidget(_wrapped(RESIZE_WARNING))

    def initializePage(self) -> None:
        wizard = self.wizard()
        m = wizard.measured()
        width, height = m.window_size

        def rect(r: Rect) -> str:
            return f"[{r.x0}, {r.y0}, {r.x1}, {r.y1}]"

        rows = "".join(
            f"<li>Page {p}, item {i}: name {rect(r.name_region)}, buy {rect(r.buy_button)}</li>"
            for p, page in ((1, m.page1), (2, m.page2))
            for i, r in enumerate(page, start=1)
        )
        exists = wizard.config_path.exists()
        self.summary.setText(
            f"<b>Game window size:</b> {width}×{height}<br>"
            f"<b>Saving to:</b> {wizard.config_path}"
            + (" (the current file is kept as a .bak)" if exists else "")
            + "<ul>"
            f"<li>“Secret Shop” title {rect(m.secret_shop_region)}</li>"
            f"<li>Gold {rect(m.gold_region)}, skystones {rect(m.skystones_region)}</li>"
            f"<li>Refresh button {rect(m.refresh_button)}</li>"
            f"{rows}"
            f"<li>Buy dialog: text {rect(m.buy_dialog.ocr_region)}, confirm {rect(m.buy_dialog.button)}</li>"
            f"<li>Refresh dialog: text {rect(m.refresh_dialog.ocr_region)}, confirm {rect(m.refresh_dialog.button)}</li>"
            "</ul>"
        )

    def validatePage(self) -> bool:
        return self.wizard().save()
