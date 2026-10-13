"""Background jobs for the GUI. Each runs on its own QThread and talks back through signals,
so the window stays responsive. A job creates its own game connection, because screen capture
must happen on the thread that set it up."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from ..abort import AbortSignal
from ..config import Config
from ..errors import StopKind, StopRun
from ..game import Game
from ..ledger import Ledger
from ..runner import Balances, check_screen, preflight, run

log = logging.getLogger(__name__)

GameFactory = Callable[[Config, AbortSignal, bool], Game]


def windows_game_factory(config: Config, abort: AbortSignal, dry_run: bool) -> Game:
    from ..windows_game import WindowsGame

    return WindowsGame(config, abort, dry_run=dry_run)


@dataclass(frozen=True)
class Progress:
    refreshes: int
    purchases: dict[str, int]
    gold_remaining: int
    skystones_remaining: int

    @classmethod
    def from_ledger(cls, ledger: Ledger) -> "Progress":
        return cls(ledger.refreshes, dict(ledger.purchases_by_item()), ledger.gold_remaining, ledger.skystones_remaining)


@dataclass(frozen=True)
class RunOutcome:
    stop: StopRun
    dry_run: bool
    # None when the run stopped before it started (e.g. the Secret Shop wasn't open).
    progress: Progress | None = None
    summary: tuple[str, ...] = ()
    record_path: Path | None = None


class BalancesWorker(QObject):
    """Check the game screen and read the starting gold and skystones."""

    succeeded = Signal(object)  # Balances
    failed = Signal(str)
    finished = Signal()

    def __init__(self, config: Config, abort: AbortSignal, game_factory: GameFactory):
        super().__init__()
        self._config = config
        self._abort = abort
        self._factory = game_factory

    @Slot()
    def work(self) -> None:
        game = None
        try:
            game = self._factory(self._config, self._abort, False)
            balances: Balances = preflight(game, self._config, sleep=self._abort.sleep)
            self.succeeded.emit(balances)
        except StopRun as stop:
            log.warning(stop.reason)
            self.failed.emit(stop.reason)
        except Exception as exc:
            log.exception("Unexpected error while reading balances")
            self.failed.emit(f"Unexpected error: {exc}")
        finally:
            if game:
                game.close()
            self.finished.emit()


class RunWorker(QObject):
    """Check the screen again, then run the buy/refresh loop with the confirmed balances."""

    progress = Signal(object)  # Progress
    stopped = Signal(object)  # RunOutcome
    finished = Signal()

    def __init__(
        self,
        config: Config,
        abort: AbortSignal,
        game_factory: GameFactory,
        *,
        gold: int,
        skystones: int,
        refreshes: int,
        dry_run: bool,
    ):
        super().__init__()
        self._config = config
        self._abort = abort
        self._factory = game_factory
        self._gold = gold
        self._skystones = skystones
        self._refreshes = refreshes
        self._dry_run = dry_run

    @Slot()
    def work(self) -> None:
        game = None
        try:
            game = self._factory(self._config, self._abort, self._dry_run)
            check_screen(game, self._config, sleep=self._abort.sleep)
        except StopRun as stop:
            log.error(stop.reason)
            self._finish(game, RunOutcome(stop, self._dry_run))
            return
        except Exception as exc:
            log.exception("Unexpected error before the run started")
            self._finish(game, RunOutcome(StopRun(StopKind.ERROR, f"Unexpected error: {exc}"), self._dry_run))
            return

        suffix = "-dry" if self._dry_run else ""
        ledger = Ledger(
            self._gold,
            self._skystones,
            path=Path(self._config.runs_dir) / f"{datetime.now():%Y%m%d-%H%M%S}{suffix}.jsonl",
            dry_run=self._dry_run,
            on_entry=lambda ledger, _entry: self.progress.emit(Progress.from_ledger(ledger)),
        )
        stop = run(game, self._config, ledger, self._refreshes, dry_run=self._dry_run, sleep=self._abort.sleep)
        self._finish(
            game,
            RunOutcome(stop, self._dry_run, Progress.from_ledger(ledger), tuple(ledger.summary_lines()), ledger.path),
        )

    def _finish(self, game: Game | None, outcome: RunOutcome) -> None:
        try:
            if game:
                game.close()
        except Exception:
            log.exception("Couldn't close the game connection")
        self.stopped.emit(outcome)
        self.finished.emit()


class LogEmitter(QObject):
    message = Signal(str)


class QtLogHandler(logging.Handler):
    """Forwards log records to the GUI. The signal is queued across threads, so any thread can log."""

    def __init__(self) -> None:
        super().__init__()
        self.emitter = LogEmitter()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.emitter.message.emit(self.format(record))
        except RuntimeError:
            # The window (and its emitter) is already gone.
            pass
