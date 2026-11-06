"""Shop actions: identify items in slots by their name, buy them, scroll, refresh."""

import logging
import time
from collections.abc import Callable

from .capture import STILL_MAX_DIFF, brightness, green_dominance, mean_difference
from .config import Config, DialogConfig, Page, Slot, WantedItem
from .errors import StopKind, StopRun
from .game import Game
from .geometry import Rect
from .ledger import Ledger

log = logging.getLogger(__name__)

# How much darker (mean grayscale, 0-255) a buy or refresh button must get after a click to count as
# covered by its dialog. Open dialogs dim the whole shop: on the real shop, buy buttons went from ~54
# to 2-25 and the Refresh button from 62 to 9, so a drop of 15 is well inside every case.
DIALOG_DIM_MIN_DROP = 15.0
# A buy button that can still be bought ("1/1 Buy") is bright green; once bought ("0/1 Buy") it's
# greyed out. Measured on the real shop at every window size tested: 25.1-26.7 available, 8.8 bought.
SOLD_OUT_MAX_GREEN = 17.0
# Clicks on a buy or refresh button before giving up when its dialog can't be found.
DIALOG_CLICK_ATTEMPTS = 3


def wait_until_still(
    game: Game,
    region: Rect,
    *,
    interval_s: float,
    timeout_s: float,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Look at the region every `interval_s` until two looks in a row match. False if it was still
    changing after `timeout_s`. A timeout of 0 doesn't wait at all."""
    checks = int(timeout_s / interval_s) if interval_s > 0 else 0
    if checks <= 0:
        return True
    previous = game.grab(region)
    for check in range(1, checks + 1):
        sleep(interval_s)
        current = game.grab(region)
        if mean_difference(previous, current) < STILL_MAX_DIFF:
            log.debug("Still after %d check(s)", check)
            return True
        previous = current
    return False


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
            label = f"page{page_index + 1}_slot{slot_index + 1}_name"
            text = self._read_name(slot.name_region, where, label)
            if text is None:
                self._ledger.record_skip(round_, page_index + 1, slot_index + 1)
                continue
            item = self._config.match_item(text)
            log.debug("Round %d, %s: %r -> %s", round_, where, text, item.name if item else "-")
            if item is None:
                continue
            log.info("Round %d, %s: found %s", round_, where, item.name)
            if self._is_sold_out(slot.buy_button):
                # Usually a refresh that didn't go through: the shop is the one already bought from.
                log.warning(
                    "Round %d, %s: %s is already bought (its buy button is greyed out); skipping it. "
                    "If it was bought last round, the last refresh didn't go through.",
                    round_, where, item.name,
                )
                self._ledger.record_sold_out(round_, item.name, page_index + 1, slot_index + 1)
                continue
            if self._ledger.gold_remaining < item.price_gold:
                raise StopRun(
                    StopKind.OUT_OF_GOLD,
                    f"Found {item.name} ({where}) but only {self._ledger.gold_remaining:,} gold is left "
                    f"and it costs {item.price_gold:,}",
                )
            self._buy(item, slot, page_index, slot_index, round_, where)

    def _buy(self, item: WantedItem, slot: Slot, page_index: int, slot_index: int, round_: int, where: str) -> None:
        if self._dry_run:
            self._game.click(slot.buy_button)
        else:
            self._click_through_dialog(
                slot.buy_button,
                self._config.buy_dialog,
                self._config.ocr.buy_text,
                name="buy dialog",
                label="buy_dialog",
                what=f"{item.name} ({where})",
            )
        self._ledger.record_purchase(round_, item.name, page_index + 1, slot_index + 1, item.price_gold)
        log.info(
            "%s %s for %s gold; %s gold left",
            "Would buy" if self._dry_run else "Bought",
            item.name,
            f"{item.price_gold:,}",
            f"{self._ledger.gold_remaining:,}",
        )

    def _is_sold_out(self, button: Rect) -> bool:
        # A dialog still fading out (after the previous purchase) would make the button look grey.
        self._wait_until_still(button)
        green = green_dominance(self._game.grab(button))
        log.debug("Buy button green dominance: %.1f", green)
        return green < SOLD_OUT_MAX_GREEN

    def _read_name(self, region: Rect, where: str, label: str) -> str | None:
        """Read a slot's item name. None when it can't be read even after recovering: the slot is skipped.

        A name that doesn't read usually means something covers the list. If a buy or refresh dialog
        is still open (its confirm click didn't land), that dialog is finished first, as the previous
        step would have. Otherwise the list may still be moving, so it's read again once it's still.
        """
        text = self._read_text(region)
        if text:
            return text
        if not self._finish_open_dialog():
            log.info("%s: no item name read; reading again once the list is still", where)
            self._wait_until_list_still()
        text = self._read_text(region)
        if text:
            return text
        self._game.save_debug(region, label, "no text read")
        log.warning("%s: couldn't read the item name; skipping this item", where)
        return None

    def _read_text(self, region: Rect) -> str:
        """The region's text with whitespace collapsed, retried; "" if nothing was read."""
        ocr = self._config.ocr
        for attempt in range(1, ocr.retries + 1):
            text = " ".join(self._game.read_text(region).split())
            if text:
                return text
            if attempt < ocr.retries:
                self._sleep(ocr.retry_delay_ms / 1000)
        return ""

    def _finish_open_dialog(self) -> bool:
        """If the buy or refresh dialog is still open, confirm it (finishing the step that opened it)."""
        config = self._config
        if config.ocr.buy_text in self._game.read_text(config.buy_dialog.ocr_region):
            log.warning("The buy dialog is still open: confirming it to finish the previous purchase")
            self._game.click(config.buy_dialog.button)
            self._wait_until_list_still()
            return True
        if config.ocr.confirm_text in self._game.read_text(config.refresh_dialog.ocr_region):
            log.warning("The refresh dialog is still open: confirming it to finish the refresh")
            self._game.click(config.refresh_dialog.button)
            self._sleep(config.timings.after_refresh_ms / 1000)
            return True
        return False

    def _click_through_dialog(
        self, button: Rect, dialog: DialogConfig, keyword: str, *, name: str, label: str, what: str
    ) -> None:
        """Click `button`, wait for its dialog, and click the dialog's confirm button.

        When the dialog's text isn't found, the button is compared with how it looked before the click.
        Clearly darker means the dialog covers it (an open dialog dims the shop; its text just wasn't
        read), so it's confirmed. Anything else means the click didn't open it, so it's clicked again.

        The "before" look waits for the button to stop changing first: right after a purchase the buy
        dialog is still fading out, and a dim "before" made a later, brighter look seem covered. That
        once confirmed a refresh that never happened.
        """
        self._wait_until_still(button)
        before = brightness(self._game.grab(button))
        for attempt in range(1, DIALOG_CLICK_ATTEMPTS + 1):
            self._game.click(button)
            if self._wait_for(dialog.ocr_region, keyword, label):
                self._game.click(dialog.button)
                return
            after = brightness(self._game.grab(button))
            if before - after >= DIALOG_DIM_MIN_DROP:
                log.warning(
                    "The %s wasn't recognised for %s, but its button got darker (%.0f → %.0f), so it's open; confirming",
                    name, what, before, after,
                )
                self._game.click(dialog.button)
                return
            log.warning(
                "The %s didn't appear for %s and its button isn't covered (%.0f → %.0f); clicking it again (%d of %d)",
                name, what, before, after, attempt, DIALOG_CLICK_ATTEMPTS,
            )
        raise StopRun(StopKind.ERROR, f"The {name} didn't appear for {what} after {DIALOG_CLICK_ATTEMPTS} clicks")

    def _wait_until_list_still(self) -> None:
        self._wait_until_still(self._config.scroll.area)

    def _wait_until_still(self, region: Rect) -> None:
        timings = self._config.timings
        wait_until_still(
            self._game,
            region,
            interval_s=timings.settle_check_ms / 1000,
            timeout_s=timings.after_scroll_ms / 1000,
            sleep=self._sleep,
        )

    def scroll_down(self) -> None:
        scroll, timings = self._config.scroll, self._config.timings
        self._game.scroll(scroll.area, scroll.notches, scroll.notch_delay_ms)
        # Instead of a fixed wait: read page 2 as soon as the list stops moving.
        if not wait_until_still(
            self._game,
            scroll.area,
            interval_s=timings.settle_check_ms / 1000,
            timeout_s=timings.after_scroll_ms / 1000,
            sleep=self._sleep,
        ):
            log.warning("The list was still moving %d ms after scrolling; reading it anyway", timings.after_scroll_ms)

    def refresh(self, round_: int) -> None:
        config = self._config
        self._click_through_dialog(
            config.refresh_button,
            config.refresh_dialog,
            config.ocr.confirm_text,
            name="refresh confirmation dialog",
            label="refresh_dialog",
            what="the refresh",
        )
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
