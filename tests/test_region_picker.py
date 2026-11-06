"""Marking regions on a screenshot (PLAN.md §9.4). Run headless with QT_QPA_PLATFORM=offscreen."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtCore")
from PySide6.QtCore import QPointF  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from e7shop.geometry import Rect  # noqa: E402
from e7shop.gui.region_picker import MarkingPanel, RegionPicker, RegionSpec, points_to_rect  # noqa: E402
from fakes import draw_shop  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_points_to_rect_orders_rounds_and_clamps():
    assert points_to_rect(QPointF(30.4, 50.6), QPointF(10.2, 20.5), (100, 100)) == Rect(10, 20, 30, 51)
    assert points_to_rect(QPointF(-5, -5), QPointF(150, 120), (100, 80)) == Rect(0, 0, 100, 80)


def test_points_to_rect_rejects_clicks():
    assert points_to_rect(QPointF(10, 10), QPointF(12, 40), (100, 100)) is None
    assert points_to_rect(QPointF(10, 10), QPointF(10, 10), (100, 100)) is None


@pytest.mark.parametrize("zoom", [1.0, 0.37, 2.5])
def test_view_positions_map_to_image_pixels_at_any_zoom(app, zoom):
    picker = RegionPicker()
    picker.resize(800, 500)
    picker.set_image(draw_shop([220]))
    picker.resetTransform()
    picker.scale(zoom, zoom)
    picker.centerOn(500, 300)
    drawn = []
    picker.drawn.connect(drawn.append)
    start = picker.mapFromScene(QPointF(450, 260))
    end = picker.mapFromScene(QPointF(560, 330))
    picker.finish_drag(picker.mapToScene(start), picker.mapToScene(end))
    (rect,) = drawn
    # A view pixel covers 1/zoom image pixels.
    slack = max(1, round(1 / zoom))
    assert all(abs(a - b) <= slack for a, b in zip(rect, (450, 260, 560, 330)))


def test_panel_marks_selected_region_checks_it_and_moves_on(app):
    image = draw_shop([220])
    panel = MarkingPanel()
    checked = []

    def check(crop):
        checked.append(crop.size)
        return crop.width > 50, f"{crop.width} wide"

    panel.load(image, [RegionSpec("a", "A", check), RegionSpec("b", "B")])
    assert panel.selected_key == "a" and not panel.all_passed()

    panel.picker.drawn.emit(Rect(10, 10, 40, 40))
    assert panel.rect("a") == Rect(10, 10, 40, 40) and not panel.passed("a")
    assert panel.selected_key == "b"  # moved on to the next unmarked region

    panel.picker.drawn.emit(Rect(100, 100, 200, 130))
    assert panel.passed("b") and not panel.all_passed()

    panel.select("a")
    panel.picker.drawn.emit(Rect(10, 10, 90, 40))  # redraw replaces it
    assert panel.rect("a") == Rect(10, 10, 90, 40) and panel.all_passed()
    assert checked[-1] == (80, 30)
    assert "80 wide" in panel.region_list.item(0).text()


def test_panel_keeps_marks_across_reloads_and_clears(app):
    image = draw_shop([220])
    panel = MarkingPanel()
    panel.load(image, [RegionSpec("a", "A")])
    panel.set_rect("a", Rect(1, 1, 20, 20))
    panel.load(image, [RegionSpec("a", "A"), RegionSpec("b", "B")])
    assert panel.rect("a") == Rect(1, 1, 20, 20) and panel.selected_key == "b"
    panel.clear()
    assert panel.rect("a") is None


def test_check_exceptions_dont_escape(app):
    def broken(_crop):
        raise RuntimeError("tesseract crashed")

    panel = MarkingPanel()
    panel.load(draw_shop([220]), [RegionSpec("a", "A", broken)])
    panel.set_rect("a", Rect(1, 1, 20, 20))
    assert not panel.passed("a")
    assert "tesseract crashed" in panel.region_list.item(0).text()
