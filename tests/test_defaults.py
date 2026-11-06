"""The built-in default layout (defaults.py) and region scaling."""

from e7shop.config import load_config
from e7shop.defaults import default_config_data, write_default_config
from e7shop.geometry import Rect


def test_default_layout_is_a_valid_16_by_9_config():
    data = default_config_data()
    size = data["window_size"]
    assert abs(size["width"] / size["height"] - 16 / 9) < 0.01
    assert "tesseract_path" not in data  # the bundled Tesseract
    assert [i["keyword"] for i in data["wanted_items"]] == ["covenant", "mystic"]


def test_default_config_data_is_a_copy():
    default_config_data()["pages"][0]["slots"].clear()
    assert len(default_config_data()["pages"][0]["slots"]) == 4


def test_write_default_config(tmp_path):
    path = tmp_path / "config.json"
    write_default_config(path)
    config = load_config(path)
    assert (config.window_size.width, config.window_size.height) == (1243, 699)
    assert len(config.pages[0].slots) == 4 and len(config.pages[1].slots) == 2


def test_rect_scaled():
    assert Rect(100, 50, 300, 150).scaled(0.5, 0.5) == Rect(50, 25, 150, 75)
    assert Rect(664, 114, 1017, 158).scaled(1206 / 1243, 678 / 699) == Rect(644, 111, 987, 153)
    # Never collapses to nothing.
    assert Rect(10, 10, 11, 11).scaled(0.1, 0.1) == Rect(1, 1, 2, 2)
