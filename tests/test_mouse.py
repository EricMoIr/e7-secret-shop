import math
import random

import pytest

from e7shop.abort import AbortSignal
from e7shop.errors import StopKind, StopRun
from e7shop.geometry import Rect
from e7shop.mouse import CLICK_PAUSE_S, Mouse, arc_path, move_duration_s, random_point_in_rect


class FakeBackend:
    def __init__(self, start=(0, 0)):
        self.pos = start
        self.moves: list[tuple[int, int]] = []
        self.clicks = 0
        self.wheel_deltas: list[int] = []

    def position(self):
        return self.pos

    def move(self, x, y):
        self.pos = (x, y)
        self.moves.append((x, y))

    def click(self):
        self.clicks += 1

    def wheel(self, delta):
        self.wheel_deltas.append(delta)


@pytest.mark.parametrize("seed", range(50))
def test_random_point_stays_in_rect(seed):
    rect = Rect(100, 200, 160, 230)
    x, y = random_point_in_rect(rect, random.Random(seed))
    assert rect.contains(x, y)


def test_random_point_in_degenerate_rect():
    assert random_point_in_rect(Rect(5, 5, 6, 6), random.Random(1)) in {(5, 5), (5, 6), (6, 5), (6, 6)}


@pytest.mark.parametrize("seed", range(20))
def test_arc_ends_exactly_on_target(seed):
    path = arc_path((10, 10), (900, 500), random.Random(seed))
    assert path[-1] == (900, 500)
    assert len(path) >= 12


@pytest.mark.parametrize("seed", range(20))
def test_arc_bows_away_from_the_straight_line_but_stays_bounded(seed):
    start, end = (0.0, 0.0), (1000.0, 0.0)
    path = arc_path(start, end, random.Random(seed), jitter_px=0)
    deviations = [y for _, y in path]
    peak = max(deviations, key=abs)
    # Control points are offset 10-30% of the distance; a cubic Bézier reaches 3/4 of that at most.
    assert 20 < abs(peak) <= 0.30 * 1000 * 0.75 + 1
    # All points bow to the same side.
    assert all(y * peak >= 0 for y in deviations)


def test_arc_for_tiny_move():
    assert arc_path((5, 5), (5.4, 5.2), random.Random(0)) == [(5.4, 5.2)]


@pytest.mark.parametrize("dist", [0, 50, 500, 3000])
def test_move_duration_bounds(dist):
    assert 0.150 <= move_duration_s(dist, random.Random(0)) <= 0.450


def test_click_in_moves_pauses_and_clicks():
    backend, sleeps = FakeBackend(), []
    mouse = Mouse(backend, AbortSignal(), random.Random(3), sleep=sleeps.append, clock=lambda: 0.0)
    rect = Rect(400, 300, 500, 340)
    point = mouse.click_in(rect)
    assert backend.pos == point and rect.contains(*point)
    assert backend.clicks == 1
    pauses = [s for s in sleeps if CLICK_PAUSE_S[0] <= s <= CLICK_PAUSE_S[1]]
    assert len(pauses) >= 2


def test_click_in_without_press():
    backend = FakeBackend()
    mouse = Mouse(backend, AbortSignal(), random.Random(3), sleep=lambda s: None)
    mouse.click_in(Rect(10, 10, 20, 20), press=False)
    assert backend.clicks == 0 and backend.moves


def test_before_press_can_stop_the_click():
    backend = FakeBackend()
    mouse = Mouse(backend, AbortSignal(), random.Random(3), sleep=lambda s: None)

    def lost_focus():
        raise StopRun(StopKind.ERROR, "lost focus")

    with pytest.raises(StopRun):
        mouse.click_in(Rect(10, 10, 20, 20), before_press=lost_focus)
    assert backend.clicks == 0


def test_abort_stops_mid_move():
    backend, abort = FakeBackend(), AbortSignal()
    calls = 0

    def sleep(_):
        nonlocal calls
        calls += 1
        if calls == 3:
            abort.trigger()

    mouse = Mouse(backend, abort, random.Random(3), sleep=sleep, clock=lambda: 0.0)
    with pytest.raises(StopRun) as info:
        mouse.click_in(Rect(900, 900, 950, 950))
    assert info.value.kind is StopKind.USER
    assert backend.clicks == 0
    assert backend.pos != (0, 0) and math.dist(backend.pos, (925, 925)) > 50


def test_scroll_in_sends_wheel_notches():
    backend = FakeBackend()
    mouse = Mouse(backend, AbortSignal(), random.Random(3), sleep=lambda s: None)
    mouse.scroll_in(Rect(100, 100, 200, 200), -5, (30, 80))
    assert backend.wheel_deltas == [-120] * 5
    assert Rect(100, 100, 200, 200).contains(*backend.pos)
