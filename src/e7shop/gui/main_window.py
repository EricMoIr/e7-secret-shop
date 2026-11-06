"""Main window: pick a config (or run setup to make one), read and confirm balances, choose how many
refreshes, run, watch progress."""

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from PySide6.QtCore import QObject, QSettings, Qt, QThread
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..abort import AbortSignal
from ..calibrate import read_raw_config
from ..config import Config, default_config_path, load_config
from ..errors import StopKind, WindowSizeMismatch
from ..logs import setup_logging
from ..runner import Balances, max_refreshes, resolve_refresh_count
from .focus import bring_to_front
from .workers import BalancesWorker, GameFactory, Progress, QtLogHandler, RunOutcome, RunWorker, windows_game_factory

log = logging.getLogger("e7shop")

INT_MAX = 2_147_483_647
STOP_TITLES = {
    StopKind.COMPLETED: "Run complete",
    StopKind.OUT_OF_SKYSTONES: "Out of skystones",
    StopKind.OUT_OF_GOLD: "Out of gold",
    StopKind.USER: "Stopped",
    StopKind.ERROR: "Run stopped with an error",
}

MessageFn = Callable[[QMessageBox.Icon, str, str], None]
# (title, text, button label) → whether the user pressed that button.
AskFn = Callable[[str, str, str], bool]
HotkeyFn = Callable[[str], Callable[[], None]]
# (config path, previous config's raw JSON, parent) → the saved config's path, or None if cancelled.
SetupFn = Callable[[Path, dict[str, Any] | None, QWidget], Path | None]


def run_setup_wizard(path: Path, base: dict[str, Any] | None, parent: QWidget) -> Path | None:
    from .setup_wizard import SetupWizard

    wizard = SetupWizard(path, base, parent=parent)
    wizard.exec()
    return wizard.saved_path


class MainWindow(QMainWindow):
    def __init__(
        self,
        config_path: str | None = None,
        *,
        game_factory: GameFactory = windows_game_factory,
        register_hotkey: HotkeyFn | None = None,
        settings: QSettings | None = None,
        setup_dialog: SetupFn = run_setup_wizard,
    ):
        super().__init__()
        self.setWindowTitle("E7 Secret Shop")
        self._settings = settings or QSettings("e7shop", "e7shop")
        self._factory = game_factory
        self._setup_dialog = setup_dialog
        # Why the config can't be used: "missing", "invalid", or None when it loaded (or can't be opened).
        self.config_problem: str | None = None
        self._config_error = ""
        self._setup_pending = False
        self._abort = AbortSignal()
        self._register_hotkey = register_hotkey or self._abort.register_hotkey
        self._unregister_hotkey: Callable[[], None] | None = None
        self._config: Config | None = None
        self._thread: QThread | None = None
        self._worker: QObject | None = None
        self._planned_refreshes = 0
        self._log_handler = QtLogHandler()
        self._log_handler.emitter.message.connect(self._append_log)
        # Replaceable so tests don't block on modal dialogs.
        self.show_message: MessageFn = self._show_message_box
        self.ask: AskFn = self._ask_box
        self.bring_back: Callable[[QWidget], None] = bring_to_front

        self._build_ui()
        self.count_spin.setValue(int(self._settings.value("refresh_count", 10)))
        self.config_edit.setText(str(config_path or self._settings.value("config_path", str(default_config_path()))))
        self.load_config(interactive=False)

    # UI

    def _build_ui(self) -> None:
        # Config
        self.config_edit = QLineEdit()
        self.config_edit.returnPressed.connect(self.load_config)
        self.browse_button = QPushButton("Browse…")
        self.browse_button.clicked.connect(self._browse_config)
        self.reload_button = QPushButton("Reload")
        self.reload_button.clicked.connect(self.load_config)
        self.setup_button = QPushButton("Run setup…")
        self.setup_button.setToolTip("Mark where everything is on screenshots of the game, and save it as this config")
        self.setup_button.clicked.connect(self.run_setup)
        self.config_status = QLabel()
        self.config_status.setWordWrap(True)
        config_row = QHBoxLayout()
        config_row.addWidget(self.config_edit, 1)
        config_row.addWidget(self.browse_button)
        config_row.addWidget(self.reload_button)
        config_row.addWidget(self.setup_button)
        config_box = QGroupBox("Config")
        config_layout = QVBoxLayout(config_box)
        config_layout.addLayout(config_row)
        config_layout.addWidget(self.config_status)

        # Balances
        self.gold_spin = self._number_spin()
        self.skystones_spin = self._number_spin()
        self.read_button = QPushButton("Read from game")
        self.read_button.clicked.connect(self.read_balances)
        self.confirm_check = QCheckBox("These values are correct")
        self.confirm_check.toggled.connect(self._update_controls)
        self.max_label = QLabel()
        balances_box = QGroupBox("Starting balances")
        balances_layout = QGridLayout(balances_box)
        balances_layout.addWidget(QLabel("Gold"), 0, 0)
        balances_layout.addWidget(self.gold_spin, 0, 1)
        balances_layout.addWidget(QLabel("Skystones"), 1, 0)
        balances_layout.addWidget(self.skystones_spin, 1, 1)
        balances_layout.addWidget(self.read_button, 0, 2, 2, 1)
        balances_layout.addWidget(self.confirm_check, 2, 0, 1, 2)
        balances_layout.addWidget(self.max_label, 3, 0, 1, 3)
        balances_layout.setColumnStretch(1, 1)
        for spin in (self.gold_spin, self.skystones_spin):
            spin.valueChanged.connect(self._on_balance_edited)

        # Run
        self.count_radio = QRadioButton("Refresh")
        self.count_spin = QSpinBox()
        self.count_spin.setRange(0, INT_MAX)
        self.count_spin.setSuffix(" times")
        self.until_radio = QRadioButton("Until out of skystones or gold")
        self.count_radio.setChecked(True)
        mode_group = QButtonGroup(self)
        mode_group.addButton(self.count_radio)
        mode_group.addButton(self.until_radio)
        self.count_radio.toggled.connect(self._update_controls)
        self.start_button = QPushButton("Start")
        self.start_button.clicked.connect(lambda: self.start_run(dry_run=False))
        self.dry_run_button = QPushButton("Dry run")
        self.dry_run_button.setToolTip("Read the shop and move the mouse without clicking. Stops at the first refresh.")
        self.dry_run_button.clicked.connect(lambda: self.start_run(dry_run=True))
        self.stop_button = QPushButton("Stop")
        self.stop_button.clicked.connect(self.stop)
        self.hotkey_label = QLabel()
        count_row = QHBoxLayout()
        count_row.addWidget(self.count_radio)
        count_row.addWidget(self.count_spin)
        count_row.addStretch(1)
        buttons_row = QHBoxLayout()
        buttons_row.addWidget(self.start_button)
        buttons_row.addWidget(self.dry_run_button)
        buttons_row.addWidget(self.stop_button)
        buttons_row.addStretch(1)
        buttons_row.addWidget(self.hotkey_label)
        run_box = QGroupBox("Run")
        run_layout = QVBoxLayout(run_box)
        run_layout.addLayout(count_row)
        run_layout.addWidget(self.until_radio)
        run_layout.addLayout(buttons_row)

        # Progress
        self.status_label = QLabel("Idle")
        self.refreshes_label = QLabel("–")
        self.gold_left_label = QLabel("–")
        self.skystones_left_label = QLabel("–")
        self.item_labels: dict[str, QLabel] = {}
        self.progress_form = QFormLayout()
        progress_box = QGroupBox("Progress")
        progress_box.setLayout(self.progress_form)

        # Log
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)

        top = QHBoxLayout()
        left = QVBoxLayout()
        left.addWidget(balances_box)
        left.addWidget(run_box)
        top.addLayout(left, 3)
        top.addWidget(progress_box, 2)
        root = QVBoxLayout()
        root.addWidget(config_box)
        root.addLayout(top)
        root.addWidget(QLabel("Log"))
        root.addWidget(self.log_view, 1)
        central = QWidget()
        central.setLayout(root)
        self.setCentralWidget(central)
        self.resize(760, 680)
        self._rebuild_progress_rows()

    @staticmethod
    def _number_spin() -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(0, INT_MAX)
        spin.setGroupSeparatorShown(True)
        spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        return spin

    def _rebuild_progress_rows(self) -> None:
        status = self.status_label.text()
        while self.progress_form.rowCount():
            # Removing a row deletes its widgets, so the labels are created fresh below.
            self.progress_form.removeRow(0)
        self.status_label = QLabel(status)
        self.status_label.setWordWrap(True)
        self.refreshes_label = QLabel("–")
        self.gold_left_label = QLabel("–")
        self.skystones_left_label = QLabel("–")
        self.progress_form.addRow("Status", self.status_label)
        self.progress_form.addRow("Refreshes", self.refreshes_label)
        self.item_labels = {}
        for item in self._config.wanted_items if self._config else []:
            self.item_labels[item.name] = QLabel("0")
            self.progress_form.addRow(item.name, self.item_labels[item.name])
        self.progress_form.addRow("Gold left", self.gold_left_label)
        self.progress_form.addRow("Skystones left", self.skystones_left_label)

    # Config

    def _browse_config(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open config", self.config_edit.text(), "JSON files (*.json)")
        if path:
            self.config_edit.setText(path)
            self.load_config()

    def load_config(self, interactive: bool = True) -> None:
        path = self.config_edit.text().strip()
        self.config_problem = None
        self._config_error = ""
        try:
            config = load_config(path)
        except FileNotFoundError:
            self.config_problem = "missing"
            self._set_config(None, f"No config at {path}. Run setup to create one.")
        except OSError as exc:
            self._set_config(None, f"Couldn't open {path}: {exc.strerror or exc}")
        except (ValidationError, ValueError) as exc:
            self.config_problem = "invalid"
            self._config_error = str(exc)
            self._set_config(None, f"Invalid config {path}. Run setup to make a new one.")
            if interactive:
                self.show_message(QMessageBox.Icon.Warning, "Invalid config", f"{exc}\n\nRun setup… makes a new one.")
        else:
            self._settings.setValue("config_path", str(Path(path).resolve()))
            self._set_config(config, f"Loaded {path}")
            log.info("Loaded config %s", path)

    # Setup

    def offer_setup_if_needed(self) -> None:
        """At startup: use the default layout when there's no config, and offer setup when the config can't be used."""
        if self.config_problem == "missing":
            self.use_default_config()
        elif self.config_problem == "invalid" and self.ask(
            "Invalid config",
            f"This config can't be used:\n\n{self._config_error}\n\nRun setup to make a new one? "
            "Your wanted items and other settings are kept.",
            "Run setup",
        ):
            self.run_setup()

    def use_default_config(self) -> None:
        """Write the built-in default layout as the config and load it. Setup stays available for when
        it doesn't line up with the game."""
        from ..defaults import write_default_config

        path = Path(self.config_edit.text().strip() or default_config_path())
        try:
            write_default_config(path)
        except OSError as exc:
            log.warning("Couldn't write the default config to %s: %s", path, exc)
            self.run_setup()
            return
        self.config_edit.setText(str(path))
        self.load_config()
        if self._config:
            log.info("Created %s with the default layout. If its regions don't line up with your game, use Run setup…", path)
            self.config_status.setText(f"Loaded the default layout ({path}). Run setup… if it doesn't line up with your game.")

    def run_setup(self) -> None:
        if self.busy:
            return
        path = Path(self.config_edit.text().strip() or default_config_path())
        saved = self._setup_dialog(path, read_raw_config(path), self)
        if saved:
            self.config_edit.setText(str(saved))
            self.load_config()

    def _set_config(self, config: Config | None, status: str) -> None:
        self._config = config
        self.config_status.setText(status)
        self.config_status.setStyleSheet("" if config else "color: #c0392b;")
        if self._unregister_hotkey:
            self._unregister_hotkey()
            self._unregister_hotkey = None
        if config:
            setup_logging(config.logs_dir, extra_handlers=[self._log_handler])
            try:
                self._unregister_hotkey = self._register_hotkey(config.abort_hotkey)
                self.hotkey_label.setText(f"{config.abort_hotkey.upper()} also stops")
            except Exception as exc:
                log.warning("Couldn't register the %s hotkey: %s", config.abort_hotkey, exc)
                self.hotkey_label.setText("")
        self._rebuild_progress_rows()
        self._update_controls()

    # Balances

    def read_balances(self) -> None:
        if not self._config or self._thread:
            return
        self._abort.reset()
        worker = BalancesWorker(self._config, self._abort, self._factory)
        # Reading focuses the game; come back to the front for the values (or the error) first.
        # Slots run in the order they're connected, so this runs before the handlers below.
        # Every slot here is a method of this window, never a lambda: PySide runs a lambda on the
        # worker's thread, and touching widgets from there froze the app.
        for signal in (worker.succeeded, worker.failed, worker.size_mismatch):
            signal.connect(self._refocus)
        worker.succeeded.connect(self._on_balances)
        worker.failed.connect(self._on_balances_failed)
        worker.size_mismatch.connect(self._offer_setup_for_size)
        self._start_worker(worker, "Reading balances…")

    def _refocus(self, *_args: object) -> None:
        self.bring_back(self)

    def _on_balances_failed(self, reason: str) -> None:
        self.show_message(QMessageBox.Icon.Warning, "Couldn't read balances", reason)

    def _on_balances(self, balances: Balances) -> None:
        self.gold_spin.setValue(min(balances.gold, INT_MAX))
        self.skystones_spin.setValue(min(balances.skystones, INT_MAX))
        # Setting the values unticks the confirmation; the user checks them before starting.
        self.confirm_check.setChecked(False)
        self.status_label.setText("Balances read. Check them, then tick “These values are correct”.")

    def _on_balance_edited(self) -> None:
        self.confirm_check.setChecked(False)
        self._update_controls()

    def _max_refreshes(self) -> int | None:
        if not self._config:
            return None
        return max_refreshes(self.skystones_spin.value(), self._config.currency.refresh_cost)

    # Run

    def start_run(self, dry_run: bool) -> None:
        if not self._config or self._thread or not self.confirm_check.isChecked():
            return
        maximum = self._max_refreshes() or 0
        requested = None if self.until_radio.isChecked() else self.count_spin.value()
        refreshes = resolve_refresh_count(requested, maximum)
        self._settings.setValue("refresh_count", self.count_spin.value())
        gold, skystones = self.gold_spin.value(), self.skystones_spin.value()

        self._planned_refreshes = refreshes
        self._on_progress(Progress(0, {}, gold, skystones))
        self._abort.reset()
        log.info(
            "Starting %s: up to %d refresh(es), %s gold, %s skystones",
            "dry run" if dry_run else "run",
            refreshes,
            f"{gold:,}",
            f"{skystones:,}",
        )
        worker = RunWorker(
            self._config,
            self._abort,
            self._factory,
            gold=gold,
            skystones=skystones,
            refreshes=refreshes,
            dry_run=dry_run,
        )
        worker.progress.connect(self._on_progress)
        worker.stopped.connect(self._on_stopped)
        self._start_worker(worker, "Dry run…" if dry_run else "Running…")

    def stop(self) -> None:
        if self._thread:
            self._abort.trigger()
            self.status_label.setText("Stopping…")

    def _on_progress(self, progress: Progress) -> None:
        self.refreshes_label.setText(f"{progress.refreshes} / {self._planned_refreshes}")
        for name, label in self.item_labels.items():
            label.setText(str(progress.purchases.get(name, 0)))
        self.gold_left_label.setText(f"{progress.gold_remaining:,}")
        self.skystones_left_label.setText(f"{progress.skystones_remaining:,}")

    def _offer_setup_for_size(self, reason: str) -> None:
        self.status_label.setText(reason)
        if self.ask("Game window shape changed", reason, "Run setup"):
            # This comes from a worker that hasn't finished yet; setup waits until it has.
            self._setup_pending = True
            self._run_pending_setup()

    def _run_pending_setup(self) -> None:
        if self._setup_pending and not self.busy:
            self._setup_pending = False
            self.run_setup()

    def _on_stopped(self, outcome: RunOutcome) -> None:
        stop = outcome.stop
        self.status_label.setText(f"{STOP_TITLES[stop.kind]}: {stop.reason}")
        if isinstance(stop, WindowSizeMismatch):
            # Stopped before starting: nothing to summarize.
            self._offer_setup_for_size(stop.reason)
            return
        if outcome.progress:
            self._on_progress(outcome.progress)
            if not outcome.dry_run:
                # Carry the tracked balances over to the next run. They still need confirming.
                self.gold_spin.setValue(outcome.progress.gold_remaining)
                self.skystones_spin.setValue(outcome.progress.skystones_remaining)
        lines = [stop.reason]
        if outcome.summary:
            lines += ["", *outcome.summary]
        if outcome.record_path:
            lines += ["", f"Run record: {outcome.record_path}"]
        icon = QMessageBox.Icon.Warning if stop.is_error else QMessageBox.Icon.Information
        title = STOP_TITLES[stop.kind] + (" (dry run)" if outcome.dry_run else "")
        self.show_message(icon, title, "\n".join(lines))

    # Threads

    def _start_worker(self, worker: BalancesWorker | RunWorker, status: str) -> None:
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.work)
        worker.finished.connect(thread.quit)
        thread.finished.connect(self._on_thread_finished)
        self._thread, self._worker = thread, worker
        self.status_label.setText(status)
        self._update_controls()
        thread.start()

    def _on_thread_finished(self) -> None:
        if self._thread:
            self._thread.wait()
            self._thread.deleteLater()
        if self._worker:
            self._worker.deleteLater()
        self._thread = self._worker = None
        self._update_controls()
        self._run_pending_setup()

    @property
    def busy(self) -> bool:
        return self._thread is not None

    # State

    def _update_controls(self) -> None:
        busy = self.busy
        loaded = self._config is not None
        for widget in (
            self.config_edit,
            self.browse_button,
            self.reload_button,
            self.setup_button,
            self.gold_spin,
            self.skystones_spin,
        ):
            widget.setEnabled(not busy)
        self.read_button.setEnabled(loaded and not busy)
        self.confirm_check.setEnabled(loaded and not busy)
        self.count_radio.setEnabled(not busy)
        self.until_radio.setEnabled(not busy)
        self.count_spin.setEnabled(not busy and self.count_radio.isChecked())
        ready = loaded and not busy and self.confirm_check.isChecked()
        self.start_button.setEnabled(ready)
        self.dry_run_button.setEnabled(ready)
        self.stop_button.setEnabled(busy)

        maximum = self._max_refreshes()
        if maximum is None:
            self.max_label.setText("")
        else:
            cost = self._config.currency.refresh_cost
            self.max_label.setText(
                f"Max refreshes: {maximum} ({cost} skystones each). "
                f"The run stops below {self._config.gold_threshold:,} gold."
            )

    def _append_log(self, line: str) -> None:
        self.log_view.appendPlainText(line)

    def _show_message_box(self, icon: QMessageBox.Icon, title: str, text: str) -> None:
        box = QMessageBox(icon, title, text, QMessageBox.StandardButton.Ok, self)
        box.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        box.exec()

    def _ask_box(self, title: str, text: str, accept_label: str) -> bool:
        box = QMessageBox(QMessageBox.Icon.Question, title, text, QMessageBox.StandardButton.NoButton, self)
        accept = box.addButton(accept_label, QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        return box.clickedButton() is accept

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._thread:
            self._abort.trigger()
            self._thread.quit()
            self._thread.wait(5000)
        if self._unregister_hotkey:
            self._unregister_hotkey()
        log.removeHandler(self._log_handler)
        super().closeEvent(event)
