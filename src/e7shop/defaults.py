"""The layout the app ships with: setup's regions for Epic Seven's Secret Shop, measured at 1243x699.

The game keeps its window 16:9 and scales everything in it with the window, so these regions work at
any window size: at run time every region is scaled to the game's current size (PLAN.md §3.1). A
resize test read every region correctly from 0.6x to 1.4x of this size. Users only run setup when
the defaults don't line up with their game.
"""

import copy
from pathlib import Path
from typing import Any

from .config import DEFAULT_WANTED_ITEMS, Config

DEFAULT_CONFIG: dict[str, Any] = {
    "window_size": {"width": 1243, "height": 699},
    "secret_shop_region": [58, 10, 214, 58],
    "currency": {"skystones_region": [869, 9, 941, 55], "gold_region": [720, 12, 841, 55]},
    "wanted_items": [dict(item) for item in DEFAULT_WANTED_ITEMS],
    "pages": [
        {
            "slots": [
                {"name_region": [664, 114, 1017, 158], "buy_button": [1061, 141, 1192, 177]},
                {"name_region": [664, 255, 1017, 299], "buy_button": [1061, 282, 1192, 318]},
                {"name_region": [664, 396, 1017, 440], "buy_button": [1061, 423, 1192, 459]},
                {"name_region": [664, 536, 1017, 580], "buy_button": [1061, 563, 1192, 599]},
            ]
        },
        {
            "slots": [
                {"name_region": [657, 464, 1010, 519], "buy_button": [1072, 491, 1191, 528]},
                {"name_region": [657, 605, 1010, 660], "buy_button": [1072, 632, 1191, 669]},
            ]
        },
    ],
    "scroll": {"area": [664, 114, 1192, 599], "notches": -5},
    "buy_dialog": {"ocr_region": [574, 124, 670, 181], "button": [634, 474, 825, 511]},
    "refresh_button": [137, 624, 308, 655],
    "refresh_dialog": {"ocr_region": [682, 427, 775, 468], "button": [668, 423, 784, 468]},
}


def default_config_data() -> dict[str, Any]:
    """A fresh copy of the default config, validated."""
    data = copy.deepcopy(DEFAULT_CONFIG)
    Config.model_validate(data)
    return data


def write_default_config(path: Path) -> None:
    """Write the default config to `path` (an existing file is kept as .bak)."""
    from .calibrate import save_config

    save_config(path, default_config_data())
