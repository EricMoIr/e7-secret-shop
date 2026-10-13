"""Rectangles and points. All config rectangles are relative to the game window's client area."""

from typing import NamedTuple

Point = tuple[float, float]


class Rect(NamedTuple):
    """Axis-aligned rectangle given by its top-left (x0, y0) and bottom-right (x1, y1) corners."""

    x0: int
    y0: int
    x1: int
    y1: int

    @property
    def width(self) -> int:
        return self.x1 - self.x0

    @property
    def height(self) -> int:
        return self.y1 - self.y0

    @property
    def center(self) -> Point:
        return ((self.x0 + self.x1) / 2, (self.y0 + self.y1) / 2)

    def offset(self, dx: int, dy: int) -> "Rect":
        return Rect(self.x0 + dx, self.y0 + dy, self.x1 + dx, self.y1 + dy)

    def contains(self, x: float, y: float) -> bool:
        return self.x0 <= x <= self.x1 and self.y0 <= y <= self.y1

    def inside(self, other: "Rect") -> bool:
        return (
            other.x0 <= self.x0
            and other.y0 <= self.y0
            and self.x1 <= other.x1
            and self.y1 <= other.y1
        )
