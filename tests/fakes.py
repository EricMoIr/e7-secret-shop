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
    """`rounds[r][p][s]` is the item name OCR reads in slot s of page p after r refreshes ("" reads nothing).

    Clicking a slot's buy button opens the buy dialog; its button buys the item. The refresh button
    opens the refresh dialog; its button moves to the next round and scrolls back to the top.

    Failure modes for the recovery tests:
    - `drop_clicks[kind]`: the next N clicks of that kind ("buy", "buy_confirm", "refresh",
      "refresh_confirm") don't register in the game.
    - `blank_name_reads`: the next N item-name reads come back empty.
    - `failed_refreshes`: the next N refresh confirmations close the dialog but don't refresh (a
      network hiccup): the round and the scroll position stay as they were.
    - A bought item keeps its name, and its buy button turns grey (`sold`).
    - `fade_after_buy`: after a purchase, the next N looks are still dimmed (the dialog fading out).
    - While a dialog is open, it dims the shop (grabs look different) and covers the item names.
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
        self.drop_clicks: dict[str, int] = {}
        self.blank_name_reads = 0
        self.failed_refreshes = 0
        # After a purchase, the next `fade_after_buy` looks at the screen are still dimmed by the
        # buy dialog fading out.
        self.fade_after_buy = 0
        self.fading_frames = 0
        self.sold: set[tuple[int, int, int]] = set()  # (round, page, slot)

        self.round = 0
        self.page = 0
        self.dialog: str | None = None
        self.selected: tuple[int, int] | None = None
        self.bought: list[tuple[int, int, int, str]] = []  # (round, page, slot, item)
        self.clicks: list[Rect] = []
        self.scrolls = 0
        self.grabs = 0
        self.frames_moving_after_scroll = 0
        self.moving_frames = 0
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
            if region == slot.name_region:
                if self.dialog:
                    return ""  # covered by the dialog
                if self.blank_name_reads:
                    self.blank_name_reads -= 1
                    return ""
                return self.rounds[self.round][self.page][s].lower()
        return ""

    def read_number(self, region: Rect) -> int | None:
        from e7shop.ocr import parse_number

        c = self.config
        if region == c.currency.gold_region:
            reads = self.gold_reads
        elif region == c.currency.skystones_region:
            reads = self.skystones_reads
        else:
            return None
        text = reads.pop(0) if len(reads) > 1 else reads[0]
        return parse_number(text)

    def _dropped(self, kind: str) -> bool:
        if self.drop_clicks.get(kind, 0) > 0:
            self.drop_clicks[kind] -= 1
            return True
        return False

    def click(self, target: Rect) -> None:
        self.clicks.append(target)
        c = self.config
        kind = (
            "buy_confirm" if self.dialog == "buy" and target == c.buy_dialog.button
            else "refresh_confirm" if self.dialog == "refresh" and target == c.refresh_dialog.button
            else "refresh" if self.dialog is None and target == c.refresh_button
            else "buy" if self.dialog is None
            else None
        )
        if kind and self._dropped(kind):
            return
        if self.dialog == "buy" and target == c.buy_dialog.button:
            r, p, s = self.round, *self.selected
            self.bought.append((r, p, s, self.rounds[r][p][s].lower()))
            self.sold.add((r, p, s))
            self.dialog = None
            self.fading_frames = self.fade_after_buy
        elif self.dialog == "refresh" and target == c.refresh_dialog.button:
            self.dialog = None
            if self.failed_refreshes:
                self.failed_refreshes -= 1
            else:
                self.round += 1
                self.page = 0
        elif self.dialog is None and target == c.refresh_button:
            self.dialog = "refresh"
        elif self.dialog is None:
            for s, slot in enumerate(c.pages[self.page].slots):
                if target == slot.buy_button and (self.round, self.page, s) not in self.sold:
                    self.dialog = "buy"
                    self.selected = (self.page, s)

    def scroll(self, area: Rect, notches: int, notch_delay_ms: tuple[int, int]) -> None:
        self.scrolls += 1
        self.page = min(self.page + 1, len(self.config.pages) - 1)  # already at the bottom: no change
        self.moving_frames = self.frames_moving_after_scroll

    def grab(self, region: Rect):
        """A flat image per (round, page); right after a scroll, `frames_moving_after_scroll` looks
        differ from each other, as if the list were still moving. A buy button is green, or grey once
        bought."""
        from PIL import Image

        self.grabs += 1
        if self.fading_frames:
            self.fading_frames -= 1
        size = (max(region.width, 1), max(region.height, 1))
        for s, slot in enumerate(self.config.pages[self.page].slots):
            if region == slot.buy_button:
                if (self.round, self.page, s) in self.sold:
                    return Image.new("RGB", size, (60, 62, 60))
                dim = 0.5 if self.dialog or self.fading_frames else 1.0
                return Image.new("RGB", size, (int(40 * dim), int(150 * dim), int(50 * dim)))
        shade = 100 + (40 * self.round + 90 * self.page) % 100
        if self.dialog or self.fading_frames:
            shade //= 3  # an open (or still fading) dialog dims everything behind it
        if self.moving_frames:
            self.moving_frames -= 1
            shade = (shade + 25 * (self.moving_frames + 1)) % 256
        return Image.new("RGB", size, (shade, shade, shade))

    def save_debug(self, region: Rect, label: str, note: str) -> None:
        self.debug_saves.append(label)

    def close(self) -> None:
        self.closed = True


class DryRunFakeGame(FakeGame):
    """Clicks move the mouse but never press, as in WindowsGame's dry run."""

    def click(self, target: Rect) -> None:
        self.clicks.append(target)


# Synthetic screenshots for setup tests. The layout matches config.example.json: rows 110 px apart,
# buy buttons at x 1440-1690, 60 px tall, item names at x 1200-1420.

ROW_PITCH = 110
BUTTON_X = (1440, 1690)
BUTTON_H = 60
NAME_X = (1200, 1420)


def draw_shop(
    button_ys: list[int],
    prices: list[int] | None = None,
    size: tuple[int, int] = (1920, 1080),
    seed: int = 1,
    names: list[str] | None = None,
):
    """A shop screenshot with one row per entry in `button_ys` (the buy buttons' top edges), each
    with its item name drawn in the name area and a price inside its buy button."""
    import numpy as np
    from PIL import Image, ImageDraw

    rng = np.random.default_rng(seed)
    noise = rng.integers(20, 45, size=(size[1], size[0], 3), dtype=np.uint8)
    image = Image.fromarray(noise, "RGB")
    draw = ImageDraw.Draw(image)
    draw.text((60, 30), "SECRET SHOP", fill=(230, 230, 230))
    prices = prices or [25_000 + 1_000 * i for i in range(len(button_ys))]
    names = names or [f"Item {i + 1}" for i in range(len(button_ys))]
    for i, (y, price, name) in enumerate(zip(button_ys, prices, names)):
        draw.rectangle((1100, y - 20, 1700, y + BUTTON_H + 20), fill=(55, 48, 66))
        draw.rectangle((1120, y - 5, 1190, y + 65), fill=(40 + 37 * i % 200, 90, 160 - 23 * i % 120))
        draw.rectangle((BUTTON_X[0], y, BUTTON_X[1], y + BUTTON_H), fill=(186, 142, 58), outline=(245, 205, 95), width=4)
        draw.rectangle((BUTTON_X[0] + 8, y + 40, BUTTON_X[1] - 8, y + 52), fill=(150, 110, 40))
        draw.text((BUTTON_X[0] + 20, y + 18), f"{price:,}", fill=(20, 20, 20))
        draw.text((NAME_X[0] + 5, y + 20), name, fill=(235, 235, 235))
    return image


def button_at(y: int):
    from e7shop.geometry import Rect

    return Rect(BUTTON_X[0], y, BUTTON_X[1], y + BUTTON_H)


def row_at(y: int):
    from e7shop.calibrate import Row
    from e7shop.geometry import Rect

    return Row(Rect(NAME_X[0], y + 10, NAME_X[1], y + 50), button_at(y))


def _crop_key(image) -> tuple:
    import hashlib

    return image.size, hashlib.md5(image.tobytes()).hexdigest()


class FakeSetupSession:
    """The `SetupSession` protocol for wizard tests. `capture()` returns `captures` in order.

    OCR answers come from `label(image, rect, text)`: reading exactly that crop of that image
    returns the text. Any other crop reads as nothing.
    """

    def __init__(self, captures: list, *, running: bool = True):
        self.captures = list(captures)
        self.running = running
        self.texts: dict[tuple, str] = {}
        self.scrolls: list[tuple] = []
        self.notches_to_bottom = 4
        self.closed = False

    def label(self, image, rect, text: str) -> None:
        self.texts[_crop_key(image.crop(tuple(rect)))] = text

    def check_game(self) -> None:
        from e7shop.errors import StopKind, StopRun

        if not self.running:
            raise StopRun(StopKind.ERROR, "EpicSeven.exe isn't running. Start Epic Seven and try again.")

    def capture(self):
        return self.captures.pop(0)

    def scroll_to_bottom(self, area, max_notches: int) -> int:
        self.scrolls.append((area, max_notches))
        return self.notches_to_bottom

    def read_text(self, image) -> str:
        return self.texts.get(_crop_key(image), "").lower()

    def read_number(self, image) -> int | None:
        from e7shop.ocr import parse_number

        return parse_number(self.texts.get(_crop_key(image), ""))

    def close(self) -> None:
        self.closed = True
