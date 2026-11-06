"""Config file models. See config.example.json."""

import json
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, PositiveInt, model_validator

from .build_flags import RELEASE
from .geometry import Rect


def _check_rect(rect: Rect) -> Rect:
    if rect.x0 >= rect.x1 or rect.y0 >= rect.y1:
        raise ValueError(f"{list(rect)} must be [x0, y0, x1, y1] with x0 < x1 and y0 < y1")
    if rect.x0 < 0 or rect.y0 < 0:
        raise ValueError(f"{list(rect)} has negative coordinates")
    return rect


ConfigRect = Annotated[Rect, AfterValidator(_check_rect)]
# OCR output is lower-cased before comparing, so every keyword is lower-cased too.
Keyword = Annotated[str, AfterValidator(str.lower)]


def default_config_path() -> Path:
    """`config.json` next to the packaged app, or in the working folder when running from source."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "config.json"
    return Path("config.json")


# What setup writes when there's no previous config to take the wanted items from.
DEFAULT_WANTED_ITEMS: tuple[dict[str, str | int], ...] = (
    {"name": "Covenant Bookmarks", "keyword": "covenant", "price_gold": 184_000},
    {"name": "Mystic Medals", "keyword": "mystic", "price_gold": 280_000},
)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class WindowSize(_Model):
    width: PositiveInt
    height: PositiveInt


class OcrConfig(_Model):
    secret_shop_text: Keyword = "secret shop"
    buy_text: Keyword = "buy"
    confirm_text: Keyword = "confirm"
    # Dialogs take a moment to animate in: check often, for up to about retries x retry_delay_ms.
    retries: PositiveInt = 8
    retry_delay_ms: int = Field(100, ge=0)
    number_consensus_reads: PositiveInt = 3
    # Tesseract page segmentation mode for text: 11 = sparse text, suits scattered UI labels.
    text_psm: int = 11
    # Separators are allowed so Tesseract doesn't read a comma as a digit. They're stripped afterwards.
    number_whitelist: str = "0123456789,."
    upscale: int = Field(2, ge=1, le=4)


class CurrencyConfig(_Model):
    skystones_region: ConfigRect
    gold_region: ConfigRect
    refresh_cost: PositiveInt = 3


class WantedItem(_Model):
    """Recognised by `keyword` in the item's name as read in the shop. `price_gold` is what it costs,
    for the ledger and the gold checks; it isn't read from the shop."""

    name: str
    keyword: Keyword = Field(min_length=1)
    price_gold: PositiveInt


class Slot(_Model):
    name_region: ConfigRect
    buy_button: ConfigRect


class Page(_Model):
    slots: list[Slot] = Field(min_length=1)


class ScrollConfig(_Model):
    area: ConfigRect
    # Setup measures how many notches reach the bottom and adds one spare.
    notches: int = -5
    notch_delay_ms: tuple[int, int] = (10, 30)


class DialogConfig(_Model):
    ocr_region: ConfigRect
    button: ConfigRect


class TimingConfig(_Model):
    after_focus_ms: int = Field(500, ge=0)
    # After scrolling, the list is looked at every settle_check_ms until two looks in a row match,
    # for at most after_scroll_ms. 0 skips the check.
    after_scroll_ms: int = Field(1500, ge=0)
    settle_check_ms: int = Field(80, ge=10)
    after_refresh_ms: int = Field(1500, ge=0)


class Config(_Model):
    # None: the Tesseract bundled with the app, or a standard install (ocr.find_tesseract).
    tesseract_path: str | None = None
    process_name: str = "EpicSeven.exe"
    window_size: WindowSize
    abort_hotkey: str = "f12"
    # Where to look for "secret shop". None means the whole window.
    secret_shop_region: ConfigRect | None = None

    ocr: OcrConfig = OcrConfig()
    currency: CurrencyConfig
    wanted_items: list[WantedItem] = Field(min_length=1)
    pages: list[Page] = Field(min_length=1)
    scroll: ScrollConfig
    buy_dialog: DialogConfig
    refresh_button: ConfigRect
    refresh_dialog: DialogConfig
    timings: TimingConfig = TimingConfig()

    runs_dir: str = "runs"
    logs_dir: str = "logs"
    debug_dir: str = "debug"
    # Debug screenshots of failed reads and of setup, in debug_dir. None: on, except in release builds.
    save_debug: bool | None = None

    @property
    def window_rect(self) -> Rect:
        return Rect(0, 0, self.window_size.width, self.window_size.height)

    @property
    def debug_captures(self) -> bool:
        return self.save_debug if self.save_debug is not None else not RELEASE

    @property
    def gold_threshold(self) -> int:
        """Below this much gold the run stops: the price of the most expensive wanted item."""
        return max(item.price_gold for item in self.wanted_items)

    def match_item(self, ocr_text: str) -> WantedItem | None:
        """The first wanted item whose keyword appears in the text (case-insensitive), if any."""
        text = ocr_text.lower()
        return next((item for item in self.wanted_items if item.keyword in text), None)

    def iter_rects(self) -> Iterator[tuple[str, Rect]]:
        """Every configured rectangle with a readable label, for validation and inspection."""
        if self.secret_shop_region:
            yield "secret_shop_region", self.secret_shop_region
        yield "currency.skystones_region", self.currency.skystones_region
        yield "currency.gold_region", self.currency.gold_region
        for p, page in enumerate(self.pages, start=1):
            for s, slot in enumerate(page.slots, start=1):
                yield f"page{p}.slot{s}.name_region", slot.name_region
                yield f"page{p}.slot{s}.buy_button", slot.buy_button
        yield "scroll.area", self.scroll.area
        yield "buy_dialog.ocr_region", self.buy_dialog.ocr_region
        yield "buy_dialog.button", self.buy_dialog.button
        yield "refresh_button", self.refresh_button
        yield "refresh_dialog.ocr_region", self.refresh_dialog.ocr_region
        yield "refresh_dialog.button", self.refresh_dialog.button

    @model_validator(mode="after")
    def _rects_inside_window(self) -> "Config":
        window = self.window_rect
        outside = [label for label, rect in self.iter_rects() if not rect.inside(window)]
        if outside:
            raise ValueError(
                f"these rectangles fall outside the {window.width}x{window.height} window: "
                + ", ".join(outside)
            )
        keywords = [item.keyword for item in self.wanted_items]
        if len(set(keywords)) != len(keywords):
            raise ValueError("wanted_items keywords must be unique")
        return self


def load_config(path: str | Path) -> Config:
    """Load and validate a config. Relative output folders are resolved against the config's folder."""
    path = Path(path)
    with open(path, encoding="utf-8") as f:
        config = Config.model_validate(json.load(f))
    base = path.resolve().parent
    return config.model_copy(
        update={
            name: str(base / getattr(config, name))
            for name in ("runs_dir", "logs_dir", "debug_dir")
            if not Path(getattr(config, name)).is_absolute()
        }
    )
