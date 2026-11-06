"""Row detection and config building for setup (PLAN.md §9.5, §9.7), on synthetic screenshots."""

import json

import pytest

from e7shop.calibrate import (
    DetectionError,
    Dialog,
    Measured,
    build_config,
    find_rows,
    format_config,
    region_changed,
    rows_above,
    save_config,
    scores_at,
    scroll_area,
)
from e7shop.config import Config, load_config
from e7shop.geometry import Rect
from fakes import EXAMPLE_CONFIG, ROW_PITCH, draw_shop, row_at

PAGE1_YS = [220, 330, 440, 550]
PAGE2_YS = [610 - k * ROW_PITCH for k in range(6)][::-1]  # 60 … 610: the bottom two are new


@pytest.mark.parametrize("marked", PAGE1_YS)
def test_find_rows_from_any_marked_row(marked):
    image = draw_shop(PAGE1_YS, prices=[184_000, 25_000, 280_000, 9_999])
    rows, pitch = find_rows(image, row_at(marked), expected=4)
    assert pitch == ROW_PITCH
    assert rows == [row_at(y) for y in PAGE1_YS]


def test_find_rows_matches_example_config_layout():
    config = load_config(EXAMPLE_CONFIG)
    rows, _ = find_rows(draw_shop(PAGE1_YS), row_at(330), expected=4)
    assert [(r.name_region, r.buy_button) for r in rows] == [
        (s.name_region, s.buy_button) for s in config.pages[0].slots
    ]


def test_find_rows_rejects_wrong_count():
    with pytest.raises(DetectionError, match="Found 3 rows"):
        find_rows(draw_shop(PAGE1_YS[:3]), row_at(220), expected=4)


def test_find_rows_rejects_uneven_spacing():
    with pytest.raises(DetectionError, match="evenly spaced"):
        find_rows(draw_shop([220, 330, 450, 550]), row_at(220), expected=4)


def test_find_rows_rejects_a_mark_that_isnt_a_button():
    # A flat patch of the background matches nothing but itself.
    marked = row_at(700)
    with pytest.raises(DetectionError):
        find_rows(draw_shop(PAGE1_YS), marked, expected=4)


def test_rows_above_for_page_two():
    assert rows_above(row_at(610), ROW_PITCH, 2) == [row_at(500), row_at(610)]


def test_scores_at_cross_checks_page_two():
    page1, page2 = draw_shop(PAGE1_YS), draw_shop(PAGE2_YS, seed=2)
    template = page1.crop(tuple(row_at(220).buy_button))
    good = scores_at(page2, template, 1440, [row_at(500), row_at(610)])
    bad = scores_at(page2, template, 1440, [row_at(555), row_at(665)])
    assert min(good) > 0.7 and max(bad) < 0.7


def test_scroll_area_covers_every_row():
    rows = [row_at(y) for y in PAGE1_YS]
    assert scroll_area(rows) == Rect(1200, 220, 1690, 610)


def test_region_changed():
    page1, page2 = draw_shop(PAGE1_YS), draw_shop(PAGE2_YS)
    area = scroll_area([row_at(y) for y in PAGE1_YS])
    assert region_changed(page1, page2, area)
    assert not region_changed(page1, draw_shop(PAGE1_YS), area)


def measured(**overrides) -> Measured:
    values = dict(
        window_size=(1920, 1080),
        secret_shop_region=Rect(40, 20, 300, 60),
        gold_region=Rect(1600, 20, 1800, 60),
        skystones_region=Rect(1400, 20, 1550, 60),
        refresh_button=Rect(100, 950, 350, 1010),
        page1=[row_at(y) for y in PAGE1_YS],
        page2=[row_at(500), row_at(610)],
        buy_dialog=Dialog(Rect(600, 300, 1300, 750), Rect(900, 650, 1100, 710)),
        refresh_dialog=Dialog(Rect(600, 300, 1300, 750), Rect(900, 650, 1100, 710)),
    )
    values.update(overrides)
    return Measured(**values)


def test_build_config_without_previous_config():
    data = build_config(measured())
    config = Config.model_validate(data)
    assert (config.window_size.width, config.window_size.height) == (1920, 1080)
    assert [i.price_gold for i in config.wanted_items] == [184_000, 280_000]
    assert config.tesseract_path is None and "tesseract_path" not in data
    assert config.scroll.area == Rect(1200, 220, 1690, 610) and config.scroll.notches == -10
    assert len(config.pages[0].slots) == 4 and len(config.pages[1].slots) == 2
    assert config.secret_shop_region == Rect(40, 20, 300, 60)


def test_build_config_keeps_settings_from_old_keyword_config():
    old = json.loads(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
    old["wanted_items"] = [{"name": "Medals", "keyword": "mystic", "price_gold": 300_000}]
    old["timings"] = {"after_focus_ms": 900}
    old["currency"]["refresh_cost"] = 4
    old["pages"][0]["slots"][0] = {"target": [1, 1, 2, 2]}
    config = Config.model_validate(build_config(measured(), old))
    assert [(i.name, i.keyword, i.price_gold) for i in config.wanted_items] == [("Medals", "mystic", 300_000)]
    assert config.timings.after_focus_ms == 900
    assert config.currency.refresh_cost == 4


def test_build_config_writes_a_chosen_tesseract_path():
    data = build_config(measured(tesseract_path=r"D:\Tesseract\tesseract.exe"))
    assert Config.model_validate(data).tesseract_path == r"D:\Tesseract\tesseract.exe"


def test_build_config_keeps_keywords_and_derives_missing_ones():
    # Price-matching configs had no keyword: it becomes the first word of the name.
    old = {
        "wanted_items": [
            {"name": "Mystic Medals", "keyword": "Mystic M", "price_gold": 280_000},
            {"name": "Covenant Bookmarks", "price_gold": 184_000},
        ]
    }
    config = Config.model_validate(build_config(measured(), old))
    assert [i.keyword for i in config.wanted_items] == ["mystic m", "covenant"]


def test_build_config_falls_back_to_defaults_when_old_settings_are_invalid():
    data = build_config(measured(), {"ocr": {"retries": 0}, "wanted_items": "nonsense"})
    assert "ocr" not in data
    Config.model_validate(data)


def test_build_config_rejects_rects_outside_the_window():
    with pytest.raises(ValueError):
        build_config(measured(window_size=(1280, 720)))


def test_saved_config_round_trips_with_one_line_rects(tmp_path):
    path = tmp_path / "config.json"
    data = build_config(measured())
    assert save_config(path, data) is None
    text = path.read_text(encoding="utf-8")
    assert '"gold_region": [1600, 20, 1800, 60]' in text
    assert load_config(path).currency.gold_region == Rect(1600, 20, 1800, 60)
    assert json.loads(format_config(data)) == data


def test_save_config_keeps_a_backup(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("old", encoding="utf-8")
    backup = save_config(path, build_config(measured()))
    assert backup == tmp_path / "config.json.bak"
    assert backup.read_text(encoding="utf-8") == "old"
