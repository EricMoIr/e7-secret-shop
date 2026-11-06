"""Preflight checks and the main buy/refresh loop."""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from .config import Config
from .errors import StopKind, StopRun, WindowSizeMismatch
from .game import Game
from .geometry import Rect
from .ledger import Ledger
from .shop import Shop, wait_for_text

log = logging.getLogger(__name__)

# How far the window's width/height ratio may be from the layout's before the run refuses to start.
ASPECT_TOLERANCE = 0.02
# Smallest scale the resize test read every region at.
MIN_TESTED_SCALE = 0.6


@dataclass(frozen=True)
class Balances:
    gold: int
    skystones: int


def preflight(game: Game, config: Config, *, sleep: Callable[[float], None] = time.sleep) -> Balances:
    """Focus the game, check the window size and the Secret Shop screen, and read starting balances."""
    check_screen(game, config, sleep=sleep)
    return read_balances(game, config, sleep=sleep)


def check_screen(game: Game, config: Config, *, sleep: Callable[[float], None] = time.sleep) -> None:
    """Focus the game and check the window size and that the Secret Shop is open."""
    game.focus()
    sleep(config.timings.after_focus_ms / 1000)

    # Regions are scaled to the window's size (WindowsGame), so any size works as long as the shape
    # matches. The game keeps its window 16:9, so a different shape means something unusual.
    width, height = game.client_size()
    reference = config.window_size
    if abs((width / height) / (reference.width / reference.height) - 1) > ASPECT_TOLERANCE:
        raise WindowSizeMismatch(
            f"The game window is {width}x{height}, a different shape from the {reference.width}x{reference.height} "
            "the layout was set up for. Resize the game to a normal 16:9 window, or run setup again."
        )
    scale = width / reference.width
    if abs(scale - 1) > 0.005:
        log.info("Game window is %dx%d: regions scaled by %.2f", width, height, scale)
    if scale < MIN_TESTED_SCALE:
        log.warning("The game window is small (%.2fx the layout); text may not read reliably", scale)

    if not wait_for_text(
        game,
        config.secret_shop_region or config.window_rect,
        config.ocr.secret_shop_text,
        attempts=config.ocr.retries,
        delay_s=config.ocr.retry_delay_ms / 1000,
        label="secret_shop",
        sleep=sleep,
    ):
        raise StopRun(StopKind.ERROR, "The Secret Shop isn't open. Go to the Secret Shop in Epic Seven and try again.")


def read_balances(game: Game, config: Config, *, sleep: Callable[[float], None] = time.sleep) -> Balances:
    gold = read_number_consensus(game, config, config.currency.gold_region, "gold", sleep=sleep)
    skystones = read_number_consensus(game, config, config.currency.skystones_region, "skystones", sleep=sleep)
    log.info("Read starting balances: %s gold, %s skystones", f"{gold:,}", f"{skystones:,}")
    return Balances(gold, skystones)


def read_number_consensus(
    game: Game, config: Config, region: Rect, label: str, *, sleep: Callable[[float], None] = time.sleep
) -> int:
    """Read the number several times and accept it only if every read agrees."""
    reads: list[int | None] = []
    for i in range(config.ocr.number_consensus_reads):
        if i:
            sleep(config.ocr.retry_delay_ms / 1000)
        reads.append(game.read_number(region))
    if None in reads or len(set(reads)) != 1:
        game.save_debug(region, label, f"reads: {reads}")
        raise StopRun(StopKind.ERROR, f"Couldn't read {label} reliably (reads: {reads})")
    value = reads[0]
    assert value is not None
    return value


def max_refreshes(skystones: int, refresh_cost: int) -> int:
    return skystones // refresh_cost


def resolve_refresh_count(requested: int | None, maximum: int) -> int:
    """`requested` None means "until out of skystones"."""
    if requested is None:
        return maximum
    if requested < 0:
        raise ValueError("the number of refreshes can't be negative")
    return min(requested, maximum)


def check_before_refresh(config: Config, ledger: Ledger, refreshes: int) -> None:
    if ledger.refreshes >= refreshes:
        raise StopRun(StopKind.COMPLETED, f"Completed {refreshes} refresh(es)")
    if ledger.skystones_remaining < config.currency.refresh_cost:
        raise StopRun(StopKind.OUT_OF_SKYSTONES, f"Only {ledger.skystones_remaining} skystones left")
    if ledger.gold_remaining < config.gold_threshold:
        raise StopRun(
            StopKind.OUT_OF_GOLD,
            f"Only {ledger.gold_remaining:,} gold left; at least {config.gold_threshold:,} is needed to keep going",
        )


def run(
    game: Game,
    config: Config,
    ledger: Ledger,
    refreshes: int,
    *,
    dry_run: bool = False,
    sleep: Callable[[float], None] = time.sleep,
) -> StopRun:
    """Scan, buy and refresh until a stop condition. Always returns why it stopped and closes the ledger.

    The shop is scanned refreshes + 1 times: once as it is now, then after each refresh.
    In a dry run nothing is clicked and the run ends at the first refresh.
    """
    shop = Shop(game, config, ledger, dry_run=dry_run, sleep=sleep)
    try:
        if ledger.initial_gold < config.gold_threshold:
            raise StopRun(
                StopKind.OUT_OF_GOLD,
                f"Not enough gold to start: {ledger.initial_gold:,} (at least {config.gold_threshold:,} needed)",
            )
        round_ = 1
        while True:
            log.info("Round %d", round_)
            for page_index in range(len(config.pages)):
                if page_index:
                    shop.scroll_down()
                shop.process_page(page_index, round_)
            check_before_refresh(config, ledger, refreshes)
            if dry_run:
                raise StopRun(StopKind.COMPLETED, "Dry run complete; the refresh was skipped")
            shop.refresh(round_)
            round_ += 1
    except StopRun as stop:
        result = stop
    except KeyboardInterrupt:
        result = StopRun(StopKind.USER, "Interrupted with Ctrl+C")
    except Exception as exc:
        log.exception("Unexpected error")
        result = StopRun(StopKind.ERROR, f"Unexpected error: {exc}")

    log.log(logging.ERROR if result.is_error else logging.INFO, "Stopped (%s): %s", result.kind.value, result.reason)
    ledger.close(result)
    return result
