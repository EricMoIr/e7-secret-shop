"""Setup calculations: find the shop's item rows, derive page 2, and build the config (PLAN.md §9.5, §9.7).

Pure functions on screenshots and rectangles: no Qt and no Win32, so all of it is unit-tested.
"""

import json
import logging
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
from pydantic import ValidationError

from .capture import mean_difference
from .config import DEFAULT_WANTED_ITEMS, Config
from .geometry import Rect

log = logging.getLogger(__name__)

# Normalized cross-correlation a buy button must reach to count as a row. Text inside
# the button differs from row to row, so even a real row never scores 1.0.
MATCH_THRESHOLD = 0.7
# How far (px) a gap between rows may be from the mean gap.
SPACING_TOLERANCE = 3
# Used when setup didn't measure the notches. Scrolling past the end of the list does nothing, so
# this always lands at the bottom.
SCROLL_NOTCHES = -10
# Setup gives up counting notches to the bottom after this many.
MAX_SCROLL_NOTCHES = 20


def run_notches(measured: int) -> int:
    """Wheel notches a run sends: what setup measured to reach the bottom, plus one spare (negative is down)."""
    return -(measured + 1)


class DetectionError(ValueError):
    """The rows couldn't be found reliably. The message says why, for the user."""


@dataclass(frozen=True)
class Row:
    """One item row of the shop: where its name is printed and its buy button."""

    name_region: Rect
    buy_button: Rect

    def shifted(self, dy: int) -> "Row":
        return Row(self.name_region.offset(0, dy), self.buy_button.offset(0, dy))

    @property
    def y(self) -> int:
        return self.buy_button.y0

    def to_json(self) -> dict[str, list[int]]:
        return {"name_region": list(self.name_region), "buy_button": list(self.buy_button)}


# Template matching


def _gray(image: Image.Image) -> np.ndarray:
    return np.asarray(image.convert("L"), dtype=np.float64)


def match_scores(image: Image.Image, template: Image.Image, x0: int) -> np.ndarray:
    """Normalized cross-correlation of `template` against `image`, sliding vertically only.

    The template stays at columns x0..x0+width. `scores[y]` is the match with the template's top
    edge at y, from -1 to 1. Flat windows (no contrast) score 0.
    """
    t = _gray(template)
    h, w = t.shape
    strip = _gray(image)[:, x0 : x0 + w]
    if strip.shape[1] != w or strip.shape[0] < h:
        return np.zeros(0)
    t = t - t.mean()
    t_norm = np.sqrt((t * t).sum())
    if t_norm == 0:
        return np.zeros(strip.shape[0] - h + 1)

    windows = np.lib.stride_tricks.sliding_window_view(strip, (h, w))[:, 0]  # (positions, h, w)
    # sum(W * (T - mean T)) equals sum((W - mean W) * (T - mean T)), because T - mean T sums to 0.
    numerator = np.einsum("yhw,hw->y", windows, t)
    n = h * w
    sums = windows.sum(axis=(1, 2))
    squares = np.einsum("yhw,yhw->y", windows, windows)
    variance = np.maximum(squares - sums * sums / n, 0)
    denominator = np.sqrt(variance) * t_norm
    return np.divide(numerator, denominator, out=np.zeros_like(numerator), where=denominator > 1e-9)


def find_peaks(scores: np.ndarray, threshold: float, min_distance: int) -> list[int]:
    """The y of each match above `threshold`, at least `min_distance` apart, best first, then sorted by y."""
    chosen: list[int] = []
    for y in np.argsort(scores)[::-1]:
        if scores[y] < threshold:
            break
        if all(abs(int(y) - c) >= min_distance for c in chosen):
            chosen.append(int(y))
    return sorted(chosen)


def check_spacing(ys: list[int], tolerance: int = SPACING_TOLERANCE) -> float:
    """Mean gap between consecutive rows. Raises DetectionError when the gaps aren't even."""
    gaps = [b - a for a, b in zip(ys, ys[1:])]
    if not gaps:
        raise DetectionError("Need at least two rows to measure the spacing")
    pitch = sum(gaps) / len(gaps)
    if any(abs(gap - pitch) > tolerance for gap in gaps):
        raise DetectionError(f"The rows aren't evenly spaced (gaps: {', '.join(map(str, gaps))} px)")
    return pitch


def find_rows(
    image: Image.Image,
    marked: Row,
    expected: int,
    *,
    threshold: float = MATCH_THRESHOLD,
    tolerance: int = SPACING_TOLERANCE,
) -> tuple[list[Row], float]:
    """Find every row on the screenshot from one marked row, by matching its buy button down the list.

    Returns the rows top to bottom and the pitch (px between rows). Raises DetectionError unless
    exactly `expected` evenly spaced rows are found, one of them the marked row.
    """
    button = marked.buy_button
    template = image.crop(tuple(button))
    scores = match_scores(image, template, button.x0)
    ys = find_peaks(scores, threshold, max(button.height, 1))
    if len(ys) != expected:
        raise DetectionError(f"Found {len(ys)} rows that look like the marked one, expected {expected}")
    if not any(abs(y - button.y0) <= 2 for y in ys):
        raise DetectionError("The marked row wasn't among the rows found")
    pitch = check_spacing(ys, tolerance)
    return [marked.shifted(y - button.y0) for y in ys], pitch


def rows_above(bottom: Row, pitch: float, count: int) -> list[Row]:
    """Page 2: the user marked the bottom row; the other new rows are one pitch apart above it."""
    return [bottom.shifted(-round((count - 1 - i) * pitch)) for i in range(count)]


def scores_at(image: Image.Image, template: Image.Image, x0: int, rows: list[Row], *, slack: int = 3) -> list[float]:
    """Best match of `template` within `slack` px of each row's button. For cross-checking page 2."""
    scores = match_scores(image, template, x0)
    result = []
    for row in rows:
        lo, hi = max(row.y - slack, 0), min(row.y + slack + 1, len(scores))
        result.append(float(scores[lo:hi].max()) if lo < hi else 0.0)
    return result


def bounding_rect(rects: list[Rect]) -> Rect:
    return Rect(min(r.x0 for r in rects), min(r.y0 for r in rects), max(r.x1 for r in rects), max(r.y1 for r in rects))


def scroll_area(rows: list[Row]) -> Rect:
    """Where to turn the wheel: the area covered by the page-1 rows."""
    return bounding_rect([r for row in rows for r in (row.name_region, row.buy_button)])


def region_changed(before: Image.Image, after: Image.Image, region: Rect, *, min_mean_diff: float = 4.0) -> bool:
    """Whether the region looks different between two screenshots (e.g. the list scrolled)."""
    return mean_difference(before.crop(tuple(region)), after.crop(tuple(region))) >= min_mean_diff


# Building the config


@dataclass(frozen=True)
class Dialog:
    ocr_region: Rect
    button: Rect

    def to_json(self) -> dict[str, list[int]]:
        return {"ocr_region": list(self.ocr_region), "button": list(self.button)}


@dataclass(frozen=True)
class Measured:
    """Everything the setup wizard measures. All rectangles are relative to the client area."""

    window_size: tuple[int, int]
    secret_shop_region: Rect
    gold_region: Rect
    skystones_region: Rect
    refresh_button: Rect
    page1: list[Row]
    page2: list[Row]
    buy_dialog: Dialog
    refresh_dialog: Dialog
    # None means the bundled Tesseract (or a standard install); nothing is written to the config.
    tesseract_path: str | None = None
    scroll_notches: int = SCROLL_NOTCHES


def build_config(measured: Measured, base: dict[str, Any] | None = None) -> dict[str, Any]:
    """The config file contents: measured coordinates, plus every other setting from `base` (the
    previous config's raw JSON) or the defaults. Validated before it's returned.

    If the previous settings make the result invalid, they're dropped in favour of the defaults:
    a broken old config must never stop setup from saving.
    """
    base = base if isinstance(base, dict) else {}

    def kept(key: str) -> dict[str, Any]:
        return {key: base[key]} if key in base else {}

    old_currency = base.get("currency") if isinstance(base.get("currency"), dict) else {}
    old_scroll = base.get("scroll") if isinstance(base.get("scroll"), dict) else {}
    width, height = measured.window_size
    data: dict[str, Any] = {
        **({"tesseract_path": measured.tesseract_path} if measured.tesseract_path else {}),
        **kept("process_name"),
        "window_size": {"width": width, "height": height},
        **kept("abort_hotkey"),
        "secret_shop_region": list(measured.secret_shop_region),
        **kept("ocr"),
        "currency": {
            "skystones_region": list(measured.skystones_region),
            "gold_region": list(measured.gold_region),
            **({"refresh_cost": old_currency["refresh_cost"]} if "refresh_cost" in old_currency else {}),
        },
        "wanted_items": wanted_items_from(base.get("wanted_items")),
        "pages": [{"slots": [row.to_json() for row in page]} for page in (measured.page1, measured.page2)],
        "scroll": {
            "area": list(scroll_area(measured.page1)),
            "notches": measured.scroll_notches,
            **({"notch_delay_ms": old_scroll["notch_delay_ms"]} if "notch_delay_ms" in old_scroll else {}),
        },
        "buy_dialog": measured.buy_dialog.to_json(),
        "refresh_button": list(measured.refresh_button),
        "refresh_dialog": measured.refresh_dialog.to_json(),
        **kept("timings"),
        **kept("runs_dir"),
        **kept("logs_dir"),
        **kept("debug_dir"),
        **kept("save_debug"),
    }
    try:
        Config.model_validate(data)
    except ValidationError:
        if not base:
            raise
        log.warning("The previous config's other settings are invalid; using the defaults instead")
        return build_config(measured, None)
    return data


def wanted_items_from(old: Any) -> list[dict[str, Any]]:
    """Keep the user's items, keywords and prices. Items from the price-matching configs have no
    keyword: it becomes the first word of the name ("Covenant Bookmarks" → "covenant")."""
    if isinstance(old, list) and old and all(isinstance(i, dict) and i.get("name") and "price_gold" in i for i in old):
        return [
            {
                "name": i["name"],
                "keyword": str(i.get("keyword") or str(i["name"]).split()[0]).lower(),
                "price_gold": i["price_gold"],
            }
            for i in old
        ]
    return [dict(item) for item in DEFAULT_WANTED_ITEMS]


_NUMBER_LIST = re.compile(r"\[\s*(-?\d+(?:\s*,\s*-?\d+)*)\s*\]")


def format_config(data: dict[str, Any]) -> str:
    """JSON with two-space indents, but rectangles kept on one line: [x0, y0, x1, y1]."""
    text = json.dumps(data, indent=2, ensure_ascii=False)
    return _NUMBER_LIST.sub(lambda m: "[" + ", ".join(n.strip() for n in m.group(1).split(",")) + "]", text) + "\n"


def read_raw_config(path: Path) -> dict[str, Any] | None:
    """A config file's JSON, valid or not, so setup can keep its other settings. None if unreadable."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def save_config(path: Path, data: dict[str, Any]) -> Path | None:
    """Write the config. An existing file is kept as <name>.bak first; returns that backup's path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if path.exists():
        backup = path.with_name(path.name + ".bak")
        shutil.copy2(path, backup)
    path.write_text(format_config(data), encoding="utf-8")
    return backup
