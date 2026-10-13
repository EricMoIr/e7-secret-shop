"""Record of everything a run spends. Gold and skystone balances are derived from it.

Starting balances are read once from the screen. Nothing else spends or earns gold or skystones
during a run, so remaining = initial - spent.
"""

import json
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, TextIO

from .errors import StopRun


@dataclass(frozen=True)
class LedgerEntry:
    timestamp: datetime
    round: int
    kind: Literal["purchase", "refresh"]
    item: str | None
    page: int | None
    slot: int | None
    gold: int
    skystones: int

    def to_json(self) -> dict:
        return {
            "type": self.kind,
            "timestamp": self.timestamp.isoformat(timespec="milliseconds"),
            "round": self.round,
            "item": self.item,
            "page": self.page,
            "slot": self.slot,
            "gold": self.gold,
            "skystones": self.skystones,
        }


class Ledger:
    def __init__(
        self,
        initial_gold: int,
        initial_skystones: int,
        *,
        path: Path | None = None,
        dry_run: bool = False,
        clock: Callable[[], datetime] = datetime.now,
    ):
        self.initial_gold = initial_gold
        self.initial_skystones = initial_skystones
        self.dry_run = dry_run
        self.entries: list[LedgerEntry] = []
        self.path = path
        self._clock = clock
        self._file: TextIO | None = None
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._file = open(path, "a", encoding="utf-8")
            self._write(
                {
                    "type": "start",
                    "timestamp": self._now(),
                    "initial_gold": initial_gold,
                    "initial_skystones": initial_skystones,
                    "dry_run": dry_run,
                }
            )

    def record_purchase(self, round_: int, item: str, page: int, slot: int, gold: int) -> LedgerEntry:
        return self._add(LedgerEntry(self._clock(), round_, "purchase", item, page, slot, gold, 0))

    def record_refresh(self, round_: int, skystones: int) -> LedgerEntry:
        return self._add(LedgerEntry(self._clock(), round_, "refresh", None, None, None, 0, skystones))

    @property
    def gold_spent(self) -> int:
        return sum(e.gold for e in self.entries)

    @property
    def skystones_spent(self) -> int:
        return sum(e.skystones for e in self.entries)

    @property
    def gold_remaining(self) -> int:
        return self.initial_gold - self.gold_spent

    @property
    def skystones_remaining(self) -> int:
        return self.initial_skystones - self.skystones_spent

    @property
    def refreshes(self) -> int:
        return sum(1 for e in self.entries if e.kind == "refresh")

    def purchases_by_item(self) -> Counter[str]:
        return Counter(e.item for e in self.entries if e.kind == "purchase")

    def summary_lines(self) -> list[str]:
        lines = [f"Refreshes:          {self.refreshes}"]
        bought = self.purchases_by_item()
        verb = "Would buy" if self.dry_run else "Bought"
        if bought:
            lines += [f"{verb} {item + ':':<20}{count}" for item, count in sorted(bought.items())]
        else:
            lines.append(f"{verb}:             nothing")
        lines += [
            f"Gold:               {self.gold_spent:,} spent, {self.gold_remaining:,} left",
            f"Skystones:          {self.skystones_spent:,} spent, {self.skystones_remaining:,} left",
        ]
        return lines

    def close(self, stop: StopRun | None = None) -> None:
        if not self._file:
            return
        self._write(
            {
                "type": "stop",
                "timestamp": self._now(),
                "kind": stop.kind.value if stop else None,
                "reason": stop.reason if stop else None,
                "refreshes": self.refreshes,
                "purchases": dict(self.purchases_by_item()),
                "gold_spent": self.gold_spent,
                "skystones_spent": self.skystones_spent,
            }
        )
        self._file.close()
        self._file = None

    def _add(self, entry: LedgerEntry) -> LedgerEntry:
        self.entries.append(entry)
        if self._file:
            self._write(entry.to_json())
        return entry

    def _write(self, record: dict) -> None:
        assert self._file
        self._file.write(json.dumps(record) + "\n")
        # Flush every record so the history survives a crash.
        self._file.flush()

    def _now(self) -> str:
        return self._clock().isoformat(timespec="milliseconds")
