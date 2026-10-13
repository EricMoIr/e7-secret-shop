"""Human-like mouse movement: curved paths, random targets, random pauses.

The path maths is pure and testable anywhere. Actual input goes through an `InputBackend`
(on Windows, `win_input.SendInputBackend`).
"""

import math
import random
import time
from collections.abc import Callable
from typing import Protocol

from .geometry import Point, Rect

CLICK_PAUSE_S = (0.100, 0.200)
WHEEL_DELTA = 120


class InputBackend(Protocol):
    def position(self) -> tuple[int, int]: ...
    def move(self, x: int, y: int) -> None: ...
    def click(self) -> None: ...
    def wheel(self, delta: int) -> None: ...


class AbortCheck(Protocol):
    def check(self) -> None: ...


def random_point_in_rect(rect: Rect, rng: random.Random) -> tuple[int, int]:
    """A random point inside the rectangle, biased toward its center (clipped Gaussian)."""

    def axis(lo: int, hi: int) -> int:
        center, sigma = (lo + hi) / 2, max((hi - lo) / 6, 0.5)
        return round(min(max(rng.gauss(center, sigma), lo), hi))

    return axis(rect.x0, rect.x1), axis(rect.y0, rect.y1)


def _ease_in_out(t: float) -> float:
    return (1 - math.cos(math.pi * t)) / 2


def arc_path(start: Point, end: Point, rng: random.Random, *, jitter_px: float = 1.5) -> list[Point]:
    """Points along a cubic Bézier arc from `start` to `end`, excluding `start` and ending exactly on `end`.

    Both control points sit on the same side of the straight line, offset by 10-30% of the distance,
    so the path bows like a wrist movement. Points are spaced with ease-in-out timing in mind:
    dense at the ends, sparse in the middle.
    """
    (sx, sy), (ex, ey) = start, end
    dx, dy = ex - sx, ey - sy
    dist = math.hypot(dx, dy)
    if dist < 1:
        return [end]

    # Unit normal to the straight line; pick a side at random.
    nx, ny = -dy / dist, dx / dist
    side = rng.choice((-1, 1))
    bow = rng.uniform(0.10, 0.30) * dist * side
    c1 = (sx + dx / 3 + nx * bow, sy + dy / 3 + ny * bow)
    c2 = (sx + 2 * dx / 3 + nx * bow * rng.uniform(0.6, 1.0), sy + 2 * dy / 3 + ny * bow * rng.uniform(0.6, 1.0))

    steps = max(12, min(80, int(dist / 12)))
    points: list[Point] = []
    for i in range(1, steps + 1):
        t = _ease_in_out(i / steps)
        u = 1 - t
        x = u**3 * sx + 3 * u**2 * t * c1[0] + 3 * u * t**2 * c2[0] + t**3 * ex
        y = u**3 * sy + 3 * u**2 * t * c1[1] + 3 * u * t**2 * c2[1] + t**3 * ey
        if i < steps:
            x += rng.uniform(-jitter_px, jitter_px)
            y += rng.uniform(-jitter_px, jitter_px)
        points.append((x, y))
    points[-1] = end
    return points


def move_duration_s(dist: float, rng: random.Random) -> float:
    """150-450 ms, longer for longer moves."""
    return min(0.450, max(0.150, 0.150 + dist * 0.00025 + rng.uniform(0, 0.080)))


class Mouse:
    def __init__(
        self,
        backend: InputBackend,
        abort: AbortCheck,
        rng: random.Random | None = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.perf_counter,
    ):
        self._backend = backend
        self._abort = abort
        self._rng = rng or random.Random()
        self._sleep = sleep
        self._clock = clock

    def move_to(self, target: tuple[int, int]) -> None:
        start = self._backend.position()
        path = arc_path(start, target, self._rng)
        duration = move_duration_s(math.dist(start, target), self._rng)
        began = self._clock()
        for i, (x, y) in enumerate(path, start=1):
            self._abort.check()
            self._backend.move(round(x), round(y))
            # Schedule against the start time so slow sleeps don't stretch the whole move.
            wait = began + duration * i / len(path) - self._clock()
            if wait > 0:
                self._sleep(wait)

    def pause(self) -> None:
        self._sleep(self._rng.uniform(*CLICK_PAUSE_S))

    def click_in(self, rect: Rect, *, before_press: Callable[[], None] = lambda: None, press: bool = True) -> tuple[int, int]:
        """Arc to a random point in `rect` (screen coordinates), wait 100-200 ms, click, wait 100-200 ms."""
        point = random_point_in_rect(rect, self._rng)
        self.move_to(point)
        self.pause()
        self._abort.check()
        before_press()
        if press:
            self._backend.click()
        self.pause()
        return point

    def scroll_in(
        self,
        rect: Rect,
        notches: int,
        notch_delay_ms: tuple[int, int],
        *,
        before_scroll: Callable[[], None] = lambda: None,
    ) -> None:
        """Arc into `rect`, then turn the wheel. Negative notches scroll down."""
        self.move_to(random_point_in_rect(rect, self._rng))
        self.pause()
        self._abort.check()
        before_scroll()
        step = WHEEL_DELTA if notches > 0 else -WHEEL_DELTA
        for _ in range(abs(notches)):
            self._abort.check()
            self._backend.wheel(step)
            self._sleep(self._rng.uniform(*notch_delay_ms) / 1000)
        self.pause()
