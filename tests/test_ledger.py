import json

from e7shop.errors import StopKind, StopRun
from e7shop.ledger import Ledger


def test_balances_are_derived_from_entries():
    ledger = Ledger(1_000_000, 30)
    ledger.record_purchase(1, "Covenant Bookmarks", 1, 2, 184_000)
    ledger.record_refresh(1, 3)
    ledger.record_purchase(2, "Mystic Medals", 2, 1, 280_000)
    ledger.record_purchase(2, "Covenant Bookmarks", 1, 1, 184_000)

    assert ledger.gold_spent == 648_000
    assert ledger.gold_remaining == 352_000
    assert ledger.skystones_spent == 3
    assert ledger.skystones_remaining == 27
    assert ledger.refreshes == 1
    assert ledger.purchases_by_item() == {"Covenant Bookmarks": 2, "Mystic Medals": 1}


def test_jsonl_file_records_start_entries_and_stop(tmp_path):
    path = tmp_path / "runs" / "run.jsonl"
    ledger = Ledger(500_000, 9, path=path)
    ledger.record_purchase(1, "Mystic Medals", 1, 3, 280_000)
    ledger.record_refresh(1, 3)
    ledger.close(StopRun(StopKind.OUT_OF_GOLD, "Only 220,000 gold left"))

    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert [r["type"] for r in records] == ["start", "purchase", "refresh", "stop"]
    assert records[0]["initial_gold"] == 500_000 and records[0]["dry_run"] is False
    assert records[1]["item"] == "Mystic Medals" and records[1]["slot"] == 3
    assert records[3]["kind"] == "out_of_gold"
    assert records[3]["purchases"] == {"Mystic Medals": 1}
    assert records[3]["gold_spent"] == 280_000


def test_entries_are_flushed_before_close(tmp_path):
    path = tmp_path / "run.jsonl"
    ledger = Ledger(500_000, 9, path=path)
    ledger.record_refresh(1, 3)
    assert len(path.read_text().splitlines()) == 2


def test_summary_lines():
    ledger = Ledger(1_000_000, 30, dry_run=True)
    assert "Would buy:             nothing" in ledger.summary_lines()
    ledger.record_purchase(1, "Mystic Medals", 1, 1, 280_000)
    text = "\n".join(ledger.summary_lines())
    assert "Would buy Mystic Medals:" in text
    assert "280,000 spent, 720,000 left" in text
