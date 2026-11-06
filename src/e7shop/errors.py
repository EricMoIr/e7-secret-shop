"""The single exception used to end a run, from any depth."""

from enum import StrEnum


class StopKind(StrEnum):
    COMPLETED = "completed"
    OUT_OF_SKYSTONES = "out_of_skystones"
    OUT_OF_GOLD = "out_of_gold"
    USER = "user"
    ERROR = "error"


class StopRun(Exception):
    def __init__(self, kind: StopKind, reason: str):
        super().__init__(reason)
        self.kind = kind
        self.reason = reason

    @property
    def is_error(self) -> bool:
        return self.kind is StopKind.ERROR

    def __repr__(self) -> str:
        return f"StopRun({self.kind.value!r}, {self.reason!r})"


class WindowSizeMismatch(StopRun):
    """The game window isn't the size setup was done at. The GUI offers to run setup again."""

    def __init__(self, reason: str):
        super().__init__(StopKind.ERROR, reason)
