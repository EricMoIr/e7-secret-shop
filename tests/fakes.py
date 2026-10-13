"""A simulated Secret Shop implementing the `Game` protocol, for tests that don't need the game."""

import json
from pathlib import Path

from e7shop.config import Config
from e7shop.geometry import Rect

EXAMPLE_CONFIG = Path(__file__).parent.parent / "config.example.json"


def example_config(**overrides) -> Config:
    data = json.loads(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
    data.update(overrides)
    return Config.model_validate(data)


def no_sleep(_seconds: float) -> None:
    pass


class FakeGame:
    """`rounds[r][p][s]` is the item name shown in slot s of page p after r refreshes.

    Clicking a slot opens the buy dialog; its button buys the item. The refresh button opens the
    refresh dialog; its button moves to the next round and scrolls back to the top.
    """

    def __init__(
        self,
        config: Config,
        rounds: list[list[list[str]]],
        *,
        gold_reads: list[str] | None = None,
        skystones_reads: list[str] | None = None,
        secret_shop: bool = True,
        client_size: tuple[int, int] | None = None,
        broken_buy_dialog: bool = False,
        broken_refresh_dialog: bool = False,
    ):
        self.config = config
        self.rounds = rounds
        self.gold_reads = list(gold_reads or ["2,000,000"])
        self.skystones_reads = list(skystones_reads or ["30"])
        self.secret_shop = secret_shop
        self.size = client_size or (config.window_size.width, config.window_size.height)
        self.broken_buy_dialog = broken_buy_dialog
        self.broken_refresh_dialog = broken_refresh_dialog

        self.round = 0
        self.page = 0
        self.dialog: str | None = None
        self.selected: tuple[int, int] | None = None
        self.bought: list[tuple[int, int, int, str]] = []  # (round, page, slot, item)
        self.clicks: list[Rect] = []
        self.scrolls = 0
        self.debug_saves: list[str] = []

    # Game protocol

    def focus(self) -> None:
        pass

    def client_size(self) -> tuple[int, int]:
        return self.size

    def read_text(self, region: Rect) -> str:
        c = self.config
        if region in (c.buy_dialog.ocr_region, c.refresh_dialog.ocr_region) and self.dialog:
            if self.dialog == "buy" and not self.broken_buy_dialog:
                return "purchase item? buy cancel"
            if self.dialog == "refresh" and not self.broken_refresh_dialog:
                return "refresh the shop? confirm cancel"
            return ""
        if region == (c.secret_shop_region or c.window_rect):
            return "secret shop" if self.secret_shop else "lobby"
        for s, slot in enumerate(c.pages[self.page].slots):
            if region == slot.ocr_region:
                return self.rounds[self.round][self.page][s].lower()
        return ""

    def read_number(self, region: Rect) -> int | None:
        from e7shop.ocr import parse_number

        c = self.config.currency
        reads = self.gold_reads if region == c.gold_region else self.skystones_reads
        text = reads.pop(0) if len(reads) > 1 else reads[0]
        return parse_number(text)

    def click(self, target: Rect) -> None:
        self.clicks.append(target)
        c = self.config
        if self.dialog == "buy" and target == c.buy_dialog.button:
            r, p, s = self.round, *self.selected
            self.bought.append((r, p, s, self.rounds[r][p][s].lower()))
            self.rounds[r][p][s] = "sold out"
            self.dialog = None
        elif self.dialog == "refresh" and target == c.refresh_dialog.button:
            self.round += 1
            self.page = 0
            self.dialog = None
        elif self.dialog is None and target == c.refresh_button:
            self.dialog = "refresh"
        elif self.dialog is None:
            for s, slot in enumerate(c.pages[self.page].slots):
                if target == slot.target:
                    self.dialog = "buy"
                    self.selected = (self.page, s)

    def scroll(self, area: Rect, notches: int, notch_delay_ms: tuple[int, int]) -> None:
        self.scrolls += 1
        self.page += 1

    def save_debug(self, region: Rect, label: str, note: str) -> None:
        self.debug_saves.append(label)


class DryRunFakeGame(FakeGame):
    """Clicks move the mouse but never press, as in WindowsGame's dry run."""

    def click(self, target: Rect) -> None:
        self.clicks.append(target)
