"""The first-run setup wizard (PLAN.md §9), walked through with a fake session and synthetic screenshots."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtCore = pytest.importorskip("PySide6.QtCore")
from PySide6.QtWidgets import QApplication  # noqa: E402

from e7shop.config import load_config  # noqa: E402
from e7shop.geometry import Rect  # noqa: E402
from e7shop.gui.setup_wizard import (  # noqa: E402
    BUY,
    CAPTURE,
    FINISH,
    ITEMS1,
    ITEMS2,
    REFRESH,
    SCREEN,
    SetupWizard,
)
from fakes import ROW_PITCH, FakeSetupSession, draw_shop, row_at  # noqa: E402

PAGE1_YS = [220, 330, 440, 550]
PAGE2_YS = [610 - k * ROW_PITCH for k in range(6)][::-1]
PAGE1_NAMES = ["Covenant Bookmarks", "Army Rescue Dog", "Mystic Medals", "Friendship Points"]
PAGE2_NAMES = ["Covenant Bookmarks", "Ruby Dragon"]

TITLE = Rect(40, 20, 300, 60)
GOLD = Rect(1600, 20, 1800, 60)
SKYSTONES = Rect(1400, 20, 1550, 60)
REFRESH_BUTTON = Rect(100, 950, 350, 1010)
DIALOG_TEXT = Rect(600, 300, 1300, 750)
DIALOG_BUTTON = Rect(900, 650, 1100, 710)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def no_bundled_tesseract(monkeypatch):
    """Most tests use the path field, which only shows when the app has no bundled Tesseract."""
    monkeypatch.setattr("e7shop.gui.setup_wizard.bundled_tesseract", lambda: None)


@pytest.fixture(autouse=True)
def close_windows(app):
    yield
    for widget in QApplication.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    app.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
    app.processEvents()


class Game:
    """Screenshots for one setup, labelled for the fake OCR."""

    def __init__(self, size=(1920, 1080)):
        self.shop = draw_shop(PAGE1_YS, size=size, seed=1, names=PAGE1_NAMES)
        self.page2 = draw_shop(PAGE2_YS, size=size, seed=2, names=["Item"] * 4 + PAGE2_NAMES)
        self.buy = draw_shop(PAGE1_YS, size=size, seed=3)
        self.refresh = draw_shop(PAGE1_YS, size=size, seed=4)
        self.session = FakeSetupSession([self.shop, self.page2, self.buy, self.refresh])
        s = self.session
        s.label(self.shop, TITLE, "SECRET SHOP")
        s.label(self.shop, GOLD, "1,234,567")
        s.label(self.shop, SKYSTONES, "300")
        for y, name in zip(PAGE1_YS, PAGE1_NAMES):
            s.label(self.shop, row_at(y).name_region, name)
        for y, name in zip(PAGE2_YS[-2:], PAGE2_NAMES):
            s.label(self.page2, row_at(y).name_region, name)
        s.label(self.buy, DIALOG_TEXT, "Purchase this item? Buy")
        s.label(self.refresh, DIALOG_TEXT, "Refresh the shop for 3 skystones? Confirm")


def make_wizard(tmp_path, game, base=None, *, path=None):
    tesseract = tmp_path / "tesseract.exe"
    tesseract.write_text("")
    game.tesseract_used = None

    def factory(tesseract_path, _base):
        game.tesseract_used = tesseract_path
        return game.session

    wizard = SetupWizard(path or tmp_path / "config.json", base, session_factory=factory)
    messages = []
    wizard.show_message = lambda icon, title, text: messages.append((title, text))
    wizard.hide_delay_s = 0
    wizard.bring_back = lambda _w: None
    wizard.show()
    wizard.page(CAPTURE).path_edit.setText(str(tesseract))
    return wizard, messages


def next_page(wizard, expected):
    assert wizard.currentPage().isComplete(), f"page {wizard.currentId()} isn't complete"
    wizard.next()
    assert wizard.currentId() == expected


def mark(panel, **rects):
    for key, rect in rects.items():
        panel.set_rect(key, rect)


def through_screen(wizard):
    assert wizard.currentId() == CAPTURE
    wizard.page(CAPTURE).capture()
    next_page(wizard, SCREEN)
    mark(wizard.page(SCREEN).panel, title=TITLE, gold=GOLD, skystones=SKYSTONES, refresh=REFRESH_BUTTON)
    next_page(wizard, ITEMS1)


def through_items(wizard, marked_y=330):
    through_screen(wizard)
    page = wizard.page(ITEMS1)
    mark(page.panel, name=row_at(marked_y).name_region, button=row_at(marked_y).buy_button)
    next_page(wizard, ITEMS2)
    page = wizard.page(ITEMS2)
    page.scroll_and_capture()
    mark(page.panel, name=row_at(610).name_region, button=row_at(610).buy_button)
    next_page(wizard, BUY)


def through_dialogs(wizard):
    for page_id, following in ((BUY, REFRESH), (REFRESH, FINISH)):
        page = wizard.page(page_id)
        page.capture()
        mark(page.panel, text=DIALOG_TEXT, button=DIALOG_BUTTON)
        next_page(wizard, following)


def test_full_setup_writes_a_working_config(app, tmp_path):
    game = Game()
    wizard, messages = make_wizard(tmp_path, game)
    through_items(wizard)
    through_dialogs(wizard)
    assert "1920×1080" in wizard.page(FINISH).summary.text()
    wizard.accept()

    assert messages == []
    assert wizard.saved_path == (tmp_path / "config.json").resolve()
    config = load_config(wizard.saved_path)
    assert (config.window_size.width, config.window_size.height) == (1920, 1080)
    assert [s.buy_button for s in config.pages[0].slots] == [row_at(y).buy_button for y in PAGE1_YS]
    assert [s.name_region for s in config.pages[1].slots] == [row_at(500).name_region, row_at(610).name_region]
    assert config.currency.gold_region == GOLD and config.secret_shop_region == TITLE
    assert config.buy_dialog.button == DIALOG_BUTTON and config.refresh_button == REFRESH_BUTTON
    assert config.scroll.area == Rect(1200, 220, 1690, 610)
    # Setup scrolled the run's scroll area, and runs send the notches it counted plus one spare.
    assert game.session.scrolls == [(config.scroll.area, 20)]
    assert config.scroll.notches == -5
    assert game.session.closed
    assert (tmp_path / "debug").is_dir()
    assert config.tesseract_path == str(tmp_path / "tesseract.exe")


def test_bundled_tesseract_needs_no_path_and_isnt_saved(app, tmp_path, monkeypatch):
    bundled = tmp_path / "bundle" / "tesseract.exe"
    bundled.parent.mkdir()
    bundled.write_text("")
    monkeypatch.setattr("e7shop.gui.setup_wizard.bundled_tesseract", lambda: bundled)
    game = Game()
    wizard, _ = make_wizard(tmp_path, game)
    wizard.page(CAPTURE).path_edit.setText(str(tmp_path / "nope.exe"))
    assert wizard.page(CAPTURE).path_box.isHidden()
    through_items(wizard)
    through_dialogs(wizard)
    wizard.accept()
    assert game.tesseract_used == str(bundled)
    assert load_config(wizard.saved_path).tesseract_path is None


def test_item_page_shows_names_and_wanted_items(app, tmp_path):
    wizard, _ = make_wizard(tmp_path, Game())
    through_screen(wizard)
    page = wizard.page(ITEMS1)
    mark(page.panel, name=row_at(440).name_region, button=row_at(440).buy_button)
    text = page.status.text()
    assert "“covenant bookmarks” → Covenant Bookmarks" in text and "“mystic medals” → Mystic Medals" in text
    assert "“friendship points”" in text and "Friendship Points →" not in text
    # Marking the two boxes is enough: the page completes once every row is found and named.
    assert page.isComplete()


def test_item_page_waits_for_both_boxes(app, tmp_path):
    wizard, _ = make_wizard(tmp_path, Game())
    through_screen(wizard)
    page = wizard.page(ITEMS1)
    mark(page.panel, name=row_at(440).name_region)
    assert not page.isComplete() and page.rows == []


def test_setup_starts_on_the_capture_page(app, tmp_path):
    wizard, _ = make_wizard(tmp_path, Game())
    assert wizard.currentId() == wizard.startId() == CAPTURE
    assert not wizard.page(CAPTURE).isComplete()
    assert not wizard.page(CAPTURE).path_box.isHidden()  # no bundled Tesseract in these tests


def test_game_not_running_is_reported_on_capture(app, tmp_path):
    game = Game()
    game.session.running = False
    wizard, messages = make_wizard(tmp_path, game)
    wizard.page(CAPTURE).capture()
    assert "isn't running" in messages[0][1]
    assert wizard.session is None and not wizard.page(CAPTURE).isComplete()


def test_missing_tesseract_is_reported_on_capture(app, tmp_path):
    wizard, messages = make_wizard(tmp_path, Game())
    wizard.page(CAPTURE).path_edit.setText(str(tmp_path / "nope.exe"))
    wizard.page(CAPTURE).capture()
    assert messages[0][0] == "Tesseract not found"
    assert not wizard.page(CAPTURE).isComplete()


def test_failed_ocr_check_blocks_next(app, tmp_path):
    wizard, _ = make_wizard(tmp_path, Game())
    wizard.page(CAPTURE).capture()
    next_page(wizard, SCREEN)
    panel = wizard.page(SCREEN).panel
    mark(panel, title=Rect(40, 20, 290, 60), gold=GOLD, skystones=SKYSTONES, refresh=REFRESH_BUTTON)
    assert not panel.passed("title") and not wizard.page(SCREEN).isComplete()
    assert "expected “secret shop”" in panel.region_list.item(0).text()


def test_detection_failure_asks_for_a_tighter_box(app, tmp_path):
    wizard, _ = make_wizard(tmp_path, Game())
    through_screen(wizard)
    page = wizard.page(ITEMS1)
    # A box that isn't a buy button finds nothing else.
    mark(page.panel, name=row_at(330).name_region, button=Rect(200, 700, 300, 760))
    assert "Couldn't find the rows" in page.status.text() and "Redraw the buy button" in page.status.text()
    assert not page.rows and not page.isComplete()

    mark(page.panel, button=row_at(330).buy_button)
    assert page.rows == [row_at(y) for y in PAGE1_YS]
    assert page.isComplete() and wizard.state.pitch == ROW_PITCH


def test_window_resized_during_setup_starts_over(app, tmp_path):
    game = Game()
    game.session.captures[2] = draw_shop(PAGE1_YS, size=(1600, 900), seed=3)
    wizard, messages = make_wizard(tmp_path, game)
    through_items(wizard)
    wizard.page(BUY).capture()

    assert messages and "changed from 1920×1080 to 1600×900" in messages[-1][1]
    assert wizard.currentId() == CAPTURE
    assert wizard.state.shop is None and wizard.page(SCREEN).panel.rect("gold") is None
    assert not wizard.page(CAPTURE).isComplete()


def test_scroll_that_moves_nothing_is_reported(app, tmp_path):
    game = Game()
    game.session.captures[1] = game.shop
    wizard, messages = make_wizard(tmp_path, game)
    through_screen(wizard)
    page = wizard.page(ITEMS1)
    mark(page.panel, name=row_at(220).name_region, button=row_at(220).buy_button)
    next_page(wizard, ITEMS2)
    wizard.page(ITEMS2).scroll_and_capture()
    assert messages[-1][0] == "The list didn't move"
    assert wizard.state.page2 is None


def test_scroll_that_never_moves_is_reported(app, tmp_path):
    game = Game()
    game.session.notches_to_bottom = 0
    wizard, messages = make_wizard(tmp_path, game)
    through_screen(wizard)
    page = wizard.page(ITEMS1)
    mark(page.panel, name=row_at(220).name_region, button=row_at(220).buy_button)
    next_page(wizard, ITEMS2)
    wizard.page(ITEMS2).scroll_and_capture()
    assert messages[-1][0] == "The list didn't move"
    assert wizard.state.page2 is None and wizard.state.notches_to_bottom is None


def test_cancel_writes_nothing(app, tmp_path):
    game = Game()
    wizard, _ = make_wizard(tmp_path, game)
    through_screen(wizard)
    wizard.reject()
    assert not (tmp_path / "config.json").exists()
    assert wizard.saved_path is None and game.session.closed


def test_rerun_keeps_previous_settings_and_backs_up(app, tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{}", encoding="utf-8")
    base = {"wanted_items": [{"name": "Medals", "keyword": "medal", "price_gold": 280000}], "timings": {"after_focus_ms": 900}}
    wizard, _ = make_wizard(tmp_path, Game(), base, path=path)
    through_items(wizard)
    through_dialogs(wizard)
    wizard.accept()
    config = load_config(path)
    assert [i.name for i in config.wanted_items] == ["Medals"]
    assert config.timings.after_focus_ms == 900
    assert (tmp_path / "config.json.bak").read_text(encoding="utf-8") == "{}"


def test_setup_screenshots_follow_save_debug(app, tmp_path):
    wizard, _ = make_wizard(tmp_path, Game(), {"save_debug": False})
    through_items(wizard)
    through_dialogs(wizard)
    wizard.accept()
    assert not (tmp_path / "debug").exists()
    assert load_config(wizard.saved_path).save_debug is False


def test_release_builds_dont_save_setup_screenshots(app, tmp_path, monkeypatch):
    monkeypatch.setattr("e7shop.gui.setup_wizard.RELEASE", True)
    wizard, _ = make_wizard(tmp_path, Game())
    through_screen(wizard)
    assert not (tmp_path / "debug").exists()
