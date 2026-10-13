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


def make_window(config_path, factory, tmp_path):
    settings = QtCore.QSettings(str(tmp_path / "settings.ini"), QtCore.QSettings.Format.IniFormat)
    window = MainWindow(str(config_path), game_factory=factory, register_hotkey=lambda key: (lambda: None), settings=settings)
    messages = []
    window.show_message = lambda icon, title, text: messages.append((icon, title, text))
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
    assert "Couldn't open" in window.config_status.text()
    assert not window.read_button.isEnabled()
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
