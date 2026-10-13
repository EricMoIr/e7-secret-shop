"""Shop actions: identify items in slots, buy them, scroll, refresh."""

import logging
import time
from collections.abc import Callable

from .config import Config, Page, Slot, WantedItem
from .errors import StopKind, StopRun
from .game import Game
from .geometry import Rect
from .ledger import Ledger

log = logging.getLogger(__name__)


def wait_for_text(
    game: Game,
    region: Rect,
    keyword: str,
    *,
    attempts: int,
    delay_s: float,
    label: str,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Read the region until it contains `keyword` (case-insensitive). Saves a debug capture on failure."""
    text = ""
    for attempt in range(1, attempts + 1):
        text = game.read_text(region)
        if keyword in text:
            log.debug("%s: found %r on attempt %d", label, keyword, attempt)
            return True
        if attempt < attempts:
            sleep(delay_s)
    log.warning("%s: %r not found after %d attempts; last OCR text: %r", label, keyword, attempts, text)
    game.save_debug(region, label, text)
    return False


class Shop:
    def __init__(
        self,
        game: Game,
        config: Config,
        ledger: Ledger,
        *,
        dry_run: bool = False,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._game = game
        self._config = config
        self._ledger = ledger
        self._dry_run = dry_run
        self._sleep = sleep

    def process_page(self, page_index: int, round_: int) -> None:
        page: Page = self._config.pages[page_index]
        for slot_index, slot in enumerate(page.slots):
            where = f"page {page_index + 1}, slot {slot_index + 1}"
            text = self._game.read_text(slot.ocr_region)
            item = self._config.match_item(text)
            log.debug("Round %d, %s: OCR %r -> %s", round_, where, text, item.name if item else "-")
            if item is None:
                continue
            log.info("Round %d, %s: found %s", round_, where, item.name)
            if self._ledger.gold_remaining < item.price_gold:
                raise StopRun(
                    StopKind.OUT_OF_GOLD,
                    f"Found {item.name} ({where}) but only {self._ledger.gold_remaining:,} gold is left "
                    f"and it costs {item.price_gold:,}",
                )
            self._buy(item, slot, page_index, slot_index, round_, where)

    def _buy(self, item: WantedItem, slot: Slot, page_index: int, slot_index: int, round_: int, where: str) -> None:
        self._game.click(slot.target)
        if not self._dry_run:
            if not self._wait_for(self._config.buy_dialog.ocr_region, self._config.ocr.buy_text, "buy_dialog"):
                raise StopRun(StopKind.ERROR, f"The buy dialog didn't appear for {item.name} ({where})")
            self._game.click(self._config.buy_dialog.button)
        self._ledger.record_purchase(round_, item.name, page_index + 1, slot_index + 1, item.price_gold)
        log.info(
            "%s %s for %s gold; %s gold left",
            "Would buy" if self._dry_run else "Bought",
            item.name,
            f"{item.price_gold:,}",
            f"{self._ledger.gold_remaining:,}",
        )

    def scroll_down(self) -> None:
        scroll = self._config.scroll
        self._game.scroll(scroll.area, scroll.notches, scroll.notch_delay_ms)
        self._sleep(self._config.timings.after_scroll_ms / 1000)

    def refresh(self, round_: int) -> None:
        self._game.click(self._config.refresh_button)
        if not self._wait_for(self._config.refresh_dialog.ocr_region, self._config.ocr.confirm_text, "refresh_dialog"):
            raise StopRun(StopKind.ERROR, "The refresh confirmation dialog didn't appear")
        self._game.click(self._config.refresh_dialog.button)
        self._ledger.record_refresh(round_, self._config.currency.refresh_cost)
        log.info("Refreshed (round %d); %d skystones left", round_, self._ledger.skystones_remaining)
        # Known limitation: the game may still be waiting on the server here. See PLAN.md §8.
        self._sleep(self._config.timings.after_refresh_ms / 1000)

    def _wait_for(self, region: Rect, keyword: str, label: str) -> bool:
        ocr = self._config.ocr
        return wait_for_text(
            self._game,
            region,
            keyword,
            attempts=ocr.retries,
            delay_s=ocr.retry_delay_ms / 1000,
            label=label,
            sleep=self._sleep,
        )
