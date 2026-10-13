import json

import pytest
from pydantic import ValidationError

from e7shop.config import Config, load_config
from e7shop.geometry import Rect
from fakes import EXAMPLE_CONFIG, example_config


def test_example_config_loads():
    config = load_config(EXAMPLE_CONFIG)
    assert config.pages[0].slots[0].target == Rect(1100, 200, 1700, 290)
    assert len(config.pages) == 2


def test_gold_threshold_is_the_most_expensive_item():
    assert example_config().gold_threshold == 280_000


def test_match_item_is_case_insensitive_substring():
    config = example_config()
    assert config.match_item("5x COVENANT Bookmarks  184,000").name == "Covenant Bookmarks"
    assert config.match_item("mystic medal x50").name == "Mystic Medals"
    assert config.match_item("Skystone x50") is None


def test_keywords_are_lower_cased():
    config = example_config(wanted_items=[{"name": "X", "keyword": "MyStIc", "price_gold": 1}])
    assert config.wanted_items[0].keyword == "mystic"
    assert config.match_item("mystic medals").name == "X"


def test_slot_ocr_region_defaults_to_target():
    slot = example_config().pages[0].slots[0]
    assert slot.ocr_region == slot.target


def _data() -> dict:
    return json.loads(EXAMPLE_CONFIG.read_text(encoding="utf-8"))


def test_rejects_inverted_rect():
    data = _data()
    data["refresh_button"] = [350, 950, 100, 1010]
    with pytest.raises(ValidationError, match="x0 < x1"):
        Config.model_validate(data)


def test_rejects_rect_outside_window():
    data = _data()
    data["pages"][1]["slots"][0]["target"] = [1100, 1000, 1700, 1200]
    with pytest.raises(ValidationError, match="page2.slot1.target"):
        Config.model_validate(data)


def test_rejects_unknown_keys():
    data = _data()
    data["refesh_button"] = [1, 1, 2, 2]
    with pytest.raises(ValidationError):
        Config.model_validate(data)


def test_rejects_duplicate_keywords():
    data = _data()
    data["wanted_items"][1]["keyword"] = "COVENANT"
    with pytest.raises(ValidationError, match="unique"):
        Config.model_validate(data)
