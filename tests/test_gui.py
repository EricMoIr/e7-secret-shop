"""GUI tests against the simulated shop. Run headless with QT_QPA_PLATFORM=offscreen (set below)."""

import json
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtCore = pytest.importorskip("PySide6.QtCore")
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from e7shop.abort import AbortSignal  # noqa: E402
from e7shop.errors import StopKind  # noqa: E402
from e7shop.gui.main_window import MainWindow  # noqa: E402
from e7shop.gui.workers import BalancesWorker, RunWorker  # noqa: E402
from fakes import EXAMPLE_CONFIG, DryRunFakeGame, FakeGame, example_config  # noqa: E402

NOTHING = [["-"] * 4, ["-"] * 2]


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def close_windows(app):
    """Close every window a test opened, so none outlives the QApplication."""
    yield
    for widget in QApplication.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    app.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
    app.processEvents()


@pytest.fixture
def config_path(tmp_path):
    data = json.loads(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
    # No real waits in tests.
    data["timings"] = {"after_focus_ms": 0, "after_scroll_ms": 0, "after_refresh_ms": 0}
    data["ocr"]["retry_delay_ms"] = 0
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


class Recorder:
    def __init__(self, signal):
        self.values = []
        signal.connect(lambda *args: self.values.append(args[0] if len(args) == 1 else args))


def make_factory(rounds, **game_kwargs):
    games = []

    def factory(config, abort, dry_run):
        game = (DryRunFakeGame if dry_run else FakeGame)(config, [list(map(list, r)) for r in rounds], **game_kwargs)
        games.append(game)
        return game

    factory.games = games
    return factory


class FakeSetup:
    """Stands in for the setup wizard: records each call and "saves" `result` (a config to copy, or None)."""

    def __init__(self, result=None):
        self.calls = []
        self.result = result

    def __call__(self, path, base, parent):
        self.calls.append((path, base))
        if self.result is None:
            return None
        path.write_text(self.result.read_text(encoding="utf-8"), encoding="utf-8")
        return path


def make_window(config_path, factory, tmp_path, *, setup=None, answer=False):
    settings = QtCore.QSettings(str(tmp_path / "settings.ini"), QtCore.QSettings.Format.IniFormat)
    window = MainWindow(
        str(config_path),
        game_factory=factory,
        register_hotkey=lambda key: (lambda: None),
        settings=settings,
        setup_dialog=setup or FakeSetup(),
    )
    messages = []
    window.show_message = lambda icon, title, text: messages.append((icon, title, text))
    window.questions = []

    def ask(title, text, button):
        window.questions.append((title, text, button))
        return answer

    window.ask = ask
    window.refocused = 0

    def bring_back(_widget):
        window.refocused += 1

    window.bring_back = bring_back
    return window, messages


def wait_until_idle(app, window, timeout=10.0):
    deadline = time.monotonic() + timeout
    while window.busy and time.monotonic() < deadline:
        app.processEvents(QtCore.QEventLoop.ProcessEventsFlag.AllEvents, 50)
    app.processEvents()
    assert not window.busy, "worker did not finish"


# Workers, run synchronously on this thread


def test_balances_worker_emits_balances(app):
    config = example_config()
    worker = BalancesWorker(config, AbortSignal(), make_factory([NOTHING], gold_reads=["1,500,000"], skystones_reads=["33"]))
    ok, failed, finished = Recorder(worker.succeeded), Recorder(worker.failed), Recorder(worker.finished)
    worker.work()
    assert [(b.gold, b.skystones) for b in ok.values] == [(1_500_000, 33)]
    assert failed.values == [] and len(finished.values) == 1


def test_balances_worker_reports_wrong_screen(app):
    worker = BalancesWorker(example_config(), AbortSignal(), make_factory([NOTHING], secret_shop=False))
    failed = Recorder(worker.failed)
    worker.work()
    assert "Secret Shop" in failed.values[0]


def test_run_worker_emits_progress_and_outcome(app, config_path):
    from e7shop.config import load_config

    config = load_config(config_path)
    rounds = [[["Covenant", "-", "-", "-"], ["Mystic", "-"]], NOTHING]
    worker = RunWorker(config, AbortSignal(), make_factory(rounds), gold=2_000_000, skystones=3, refreshes=1, dry_run=False)
    progress, stopped = Recorder(worker.progress), Recorder(worker.stopped)
    worker.work()

    assert [p.purchases for p in progress.values] == [
        {"Covenant Bookmarks": 1},
        {"Covenant Bookmarks": 1, "Mystic Medals": 1},
        {"Covenant Bookmarks": 1, "Mystic Medals": 1},  # the refresh
    ]
    outcome = stopped.values[0]
    assert outcome.stop.kind is StopKind.COMPLETED
    assert outcome.progress.skystones_remaining == 0
    assert outcome.record_path.exists()


def test_run_worker_stops_before_start_on_wrong_screen(app):
    worker = RunWorker(
        example_config(), AbortSignal(), make_factory([NOTHING], secret_shop=False),
        gold=2_000_000, skystones=30, refreshes=1, dry_run=False,
    )
    stopped = Recorder(worker.stopped)
    worker.work()
    outcome = stopped.values[0]
    assert outcome.stop.is_error and outcome.progress is None


# Main window


def test_window_loads_config_and_gates_start_on_confirmation(app, config_path, tmp_path):
    window, _ = make_window(config_path, make_factory([NOTHING]), tmp_path)
    assert "Loaded" in window.config_status.text()
    assert set(window.item_labels) == {"Covenant Bookmarks", "Mystic Medals"}
    assert not window.start_button.isEnabled()

    window.gold_spin.setValue(1_000_000)
    window.skystones_spin.setValue(31)
    assert "Max refreshes: 10" in window.max_label.text()
    window.confirm_check.setChecked(True)
    assert window.start_button.isEnabled() and window.dry_run_button.isEnabled()

    # Editing a value requires confirming again.
    window.skystones_spin.setValue(30)
    assert not window.confirm_check.isChecked() and not window.start_button.isEnabled()


def test_window_reports_missing_config(app, tmp_path):
    window, messages = make_window(tmp_path / "nope.json", make_factory([NOTHING]), tmp_path)
    assert "No config at" in window.config_status.text() and "Run setup" in window.config_status.text()
    assert window.config_problem == "missing"
    assert not window.read_button.isEnabled()
    assert messages == []


def test_missing_config_uses_the_default_layout(app, tmp_path):
    target = tmp_path / "new" / "config.json"
    target.parent.mkdir()
    setup = FakeSetup()
    window, _ = make_window(target, make_factory([NOTHING]), tmp_path, setup=setup)
    window.offer_setup_if_needed()
    assert setup.calls == []  # no setup needed
    assert target.exists() and window.config_problem is None
    assert "default layout" in window.config_status.text()
    assert window.read_button.isEnabled()


def test_run_setup_can_replace_the_defaults(app, config_path, tmp_path):
    target = tmp_path / "new" / "config.json"
    target.parent.mkdir()
    setup = FakeSetup(result=config_path)
    window, _ = make_window(target, make_factory([NOTHING]), tmp_path, setup=setup)
    window.offer_setup_if_needed()
    window.run_setup()
    assert len(setup.calls) == 1 and setup.calls[0][1]["window_size"] == {"width": 1243, "height": 699}
    assert "Loaded" in window.config_status.text()


def test_invalid_config_at_boot_offers_setup_with_its_settings(app, config_path, tmp_path):
    config_path.write_text('{"timings": {"after_focus_ms": 900}}', encoding="utf-8")
    setup = FakeSetup()
    window, _ = make_window(config_path, make_factory([NOTHING]), tmp_path, setup=setup, answer=True)
    window.offer_setup_if_needed()
    assert window.questions[0][0] == "Invalid config" and window.questions[0][2] == "Run setup"
    assert setup.calls == [(config_path, {"timings": {"after_focus_ms": 900}})]


def test_invalid_config_at_boot_can_be_declined(app, config_path, tmp_path):
    config_path.write_text("{}", encoding="utf-8")
    setup = FakeSetup()
    window, _ = make_window(config_path, make_factory([NOTHING]), tmp_path, setup=setup, answer=False)
    window.offer_setup_if_needed()
    assert window.questions and setup.calls == []


def test_valid_config_at_boot_runs_nothing(app, config_path, tmp_path):
    setup = FakeSetup()
    window, _ = make_window(config_path, make_factory([NOTHING]), tmp_path, setup=setup)
    window.offer_setup_if_needed()
    assert setup.calls == [] and window.questions == []


def test_wrong_window_size_offers_setup(app, config_path, tmp_path):
    setup = FakeSetup()
    window, messages = make_window(
        config_path, make_factory([NOTHING], client_size=(1600, 1200)), tmp_path, setup=setup, answer=True
    )
    window.read_balances()
    wait_until_idle(app, window)
    title, text, button = window.questions[0]
    assert title == "Game window shape changed" and "1600x1200" in text and "1920x1080" in text
    assert button == "Run setup" and len(setup.calls) == 1
    assert messages == []


def test_wrong_window_size_at_run_start_offers_setup(app, config_path, tmp_path):
    window, messages = make_window(config_path, make_factory([NOTHING], client_size=(1600, 1200)), tmp_path)
    window.gold_spin.setValue(1_000_000)
    window.skystones_spin.setValue(30)
    window.confirm_check.setChecked(True)
    window.start_run(dry_run=False)
    wait_until_idle(app, window)
    assert window.questions[0][0] == "Game window shape changed"
    assert messages == []


def test_window_reports_invalid_config_when_reloaded(app, config_path, tmp_path):
    window, messages = make_window(config_path, make_factory([NOTHING]), tmp_path)
    config_path.write_text("{}", encoding="utf-8")
    window.load_config()
    assert "Invalid config" in window.config_status.text()
    assert messages and messages[0][1] == "Invalid config"


def test_read_balances_fills_values_unconfirmed(app, config_path, tmp_path):
    factory = make_factory([NOTHING], gold_reads=["1,234,567"], skystones_reads=["42"])
    window, _ = make_window(config_path, factory, tmp_path)
    window.confirm_check.setChecked(True)
    window.read_balances()
    wait_until_idle(app, window)
    assert window.gold_spin.value() == 1_234_567 and window.skystones_spin.value() == 42
    assert not window.confirm_check.isChecked()
    assert window.refocused == 1


def test_read_balances_failure_also_refocuses_before_the_message(app, config_path, tmp_path):
    window, messages = make_window(config_path, make_factory([NOTHING], secret_shop=False), tmp_path)
    order = []
    window.bring_back = lambda _w: order.append("refocus")
    window.show_message = lambda icon, title, text: order.append(title)
    window.read_balances()
    wait_until_idle(app, window)
    assert order == ["refocus", "Couldn't read balances"]


def test_full_run_updates_progress_and_carries_balances_over(app, config_path, tmp_path):
    # 9 skystones: 3 refreshes, so 4 scans.
    rounds = [[["Covenant", "-", "-", "-"], ["-", "-"]], [["-", "Mystic", "-", "-"], ["-", "-"]], NOTHING, NOTHING]
    window, messages = make_window(config_path, make_factory(rounds), tmp_path)
    window.gold_spin.setValue(1_000_000)
    window.skystones_spin.setValue(9)
    window.until_radio.setChecked(True)
    window.confirm_check.setChecked(True)

    window.start_run(dry_run=False)
    assert window.busy and window.stop_button.isEnabled() and not window.start_button.isEnabled()
    wait_until_idle(app, window)

    assert window.refreshes_label.text() == "3 / 3"
    assert window.item_labels["Covenant Bookmarks"].text() == "1"
    assert window.item_labels["Mystic Medals"].text() == "1"
    # 1,000,000 - 184,000 - 280,000; still above the 280,000 threshold, so every refresh ran.
    assert window.gold_left_label.text() == "536,000"
    icon, title, text = messages[-1]
    assert title == "Run complete" and "Bought Mystic Medals:" in text
    # The tracked balances carry over, unconfirmed.
    assert window.gold_spin.value() == 536_000 and window.skystones_spin.value() == 0
    assert not window.confirm_check.isChecked() and not window.start_button.isEnabled()


def test_dry_run_keeps_balances(app, config_path, tmp_path):
    rounds = [[["Covenant", "-", "-", "-"], ["Mystic", "-"]]]
    factory = make_factory(rounds)
    window, messages = make_window(config_path, factory, tmp_path)
    window.gold_spin.setValue(1_000_000)
    window.skystones_spin.setValue(30)
    window.confirm_check.setChecked(True)
    window.start_run(dry_run=True)
    wait_until_idle(app, window)
    assert factory.games[0].bought == []
    assert window.gold_spin.value() == 1_000_000
    assert messages[-1][1] == "Run complete (dry run)"
    assert "Would buy Covenant Bookmarks:" in messages[-1][2]


def test_stop_button_aborts_run(app, config_path, tmp_path):
    class SlowGame(FakeGame):
        # Item names are read as text: slowing that down keeps the run going until Stop lands.
        def read_text(self, region):
            time.sleep(0.02)
            return super().read_text(region)

    def factory(config, abort, dry_run):
        return SlowGame(config, [NOTHING] * 20)

    window, messages = make_window(config_path, factory, tmp_path)
    window.gold_spin.setValue(10_000_000)
    window.skystones_spin.setValue(60)
    window.until_radio.setChecked(True)
    window.confirm_check.setChecked(True)
    window.start_run(dry_run=False)
    app.processEvents()
    window.stop()
    wait_until_idle(app, window)
    icon, title, _ = messages[-1]
    assert title == "Stopped" and icon == QMessageBox.Icon.Information


def test_enlarge_font_adds_two_pixels(app):
    from PySide6.QtGui import QFont

    from e7shop.gui.app import enlarge_font

    original = app.font()
    try:
        app.setFont(QFont("Arial", 9))
        enlarge_font(app)
        assert app.font().pointSizeF() == 10.5  # 9 pt + 2 px (1.5 pt)
        pixel_font = QFont("Arial")
        pixel_font.setPixelSize(12)
        app.setFont(pixel_font)
        enlarge_font(app)
        assert app.font().pixelSize() == 14
    finally:
        app.setFont(original)


def test_balances_results_are_handled_on_the_gui_thread(app, config_path, tmp_path):
    """The window must only be touched from the GUI thread; a lambda slot ran on the worker's
    thread and froze the app on Windows."""
    import threading

    for factory in (make_factory([NOTHING]), make_factory([NOTHING], secret_shop=False)):
        window, _ = make_window(config_path, factory, tmp_path)
        threads = []
        window.bring_back = lambda _w: threads.append(threading.current_thread())
        window.show_message = lambda *_args: threads.append(threading.current_thread())
        window.read_balances()
        wait_until_idle(app, window)
        assert threads and all(t is threading.main_thread() for t in threads)


def test_a_resized_window_of_the_same_shape_runs(app, config_path, tmp_path):
    window, messages = make_window(config_path, make_factory([NOTHING], client_size=(960, 540)), tmp_path)
    window.read_balances()
    wait_until_idle(app, window)
    assert window.questions == [] and messages == []
    assert window.gold_spin.value() == 2_000_000
