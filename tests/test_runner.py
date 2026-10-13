"""The run loop and every stop condition from PLAN.md §3.6, against a simulated shop."""

import pytest

from e7shop.errors import StopKind, StopRun
from e7shop.ledger import Ledger
from e7shop.runner import max_refreshes, preflight, resolve_refresh_count, run
from fakes import DryRunFakeGame, FakeGame, example_config, no_sleep

CONFIG = example_config()
NOTHING = [["-"] * 4, ["-"] * 2]


def shop(page1: list[str], page2: list[str]) -> list[list[str]]:
    return [page1 + ["-"] * (4 - len(page1)), page2 + ["-"] * (2 - len(page2))]


def run_game(game, gold=2_000_000, skystones=30, refreshes=10, **kwargs):
    ledger = Ledger(gold, skystones)
    stop = run(game, CONFIG, ledger, refreshes, sleep=no_sleep, **kwargs)
    return stop, ledger


# preflight


def test_preflight_reads_balances():
    game = FakeGame(CONFIG, [NOTHING], gold_reads=["1,234,567"], skystones_reads=["42"])
    balances = preflight(game, CONFIG, sleep=no_sleep)
    assert (balances.gold, balances.skystones) == (1_234_567, 42)


def test_preflight_requires_secret_shop():
    game = FakeGame(CONFIG, [NOTHING], secret_shop=False)
    with pytest.raises(StopRun, match="Secret Shop") as info:
        preflight(game, CONFIG, sleep=no_sleep)
    assert info.value.is_error
    assert game.debug_saves == ["secret_shop"]


def test_preflight_requires_matching_window_size():
    game = FakeGame(CONFIG, [NOTHING], client_size=(1280, 720))
    with pytest.raises(StopRun, match="1280x720"):
        preflight(game, CONFIG, sleep=no_sleep)


def test_preflight_requires_consistent_currency_reads():
    game = FakeGame(CONFIG, [NOTHING], gold_reads=["1,234,567", "1,284,567", "1,234,567"])
    with pytest.raises(StopRun, match="gold"):
        preflight(game, CONFIG, sleep=no_sleep)
    assert game.debug_saves == ["gold"]


def test_preflight_fails_when_no_digits_read():
    game = FakeGame(CONFIG, [NOTHING], skystones_reads=[""])
    with pytest.raises(StopRun, match="skystones"):
        preflight(game, CONFIG, sleep=no_sleep)


# refresh count


def test_max_refreshes_is_floor_of_skystones_over_cost():
    assert max_refreshes(30, 3) == 10
    assert max_refreshes(32, 3) == 10
    assert max_refreshes(2, 3) == 0


def test_resolve_refresh_count():
    assert resolve_refresh_count(None, 10) == 10
    assert resolve_refresh_count(4, 10) == 4
    assert resolve_refresh_count(50, 10) == 10
    with pytest.raises(ValueError):
        resolve_refresh_count(-1, 10)


# run loop


def test_buys_wanted_items_on_both_pages_and_refreshes_n_times():
    rounds = [
        shop(["Skystone", "Covenant Bookmarks x5"], ["Mystic Medal x50"]),
        shop([], []),
        shop(["mystic medal"], ["COVENANT bookmarks", "Covenant Bookmarks"]),
    ]
    game = FakeGame(CONFIG, rounds)
    stop, ledger = run_game(game, refreshes=2)

    assert stop.kind is StopKind.COMPLETED
    assert game.bought == [
        (0, 0, 1, "covenant bookmarks x5"),
        (0, 1, 0, "mystic medal x50"),
        (2, 0, 0, "mystic medal"),
        (2, 1, 0, "covenant bookmarks"),
        (2, 1, 1, "covenant bookmarks"),
    ]
    assert ledger.refreshes == 2
    assert ledger.purchases_by_item() == {"Covenant Bookmarks": 3, "Mystic Medals": 2}
    assert ledger.gold_spent == 3 * 184_000 + 2 * 280_000
    assert ledger.skystones_spent == 6
    # Scanned N + 1 = 3 times; scrolled once per scan.
    assert game.scrolls == 3


def test_zero_refreshes_scans_once():
    game = FakeGame(CONFIG, [shop(["Covenant"], [])])
    stop, ledger = run_game(game, refreshes=0)
    assert stop.kind is StopKind.COMPLETED
    assert ledger.refreshes == 0 and len(game.bought) == 1


def test_stops_when_out_of_skystones():
    game = FakeGame(CONFIG, [NOTHING] * 5)
    # Asked for more refreshes than the skystones allow (the CLI caps this; the loop guards it too).
    stop, ledger = run_game(game, skystones=7, refreshes=10)
    assert stop.kind is StopKind.OUT_OF_SKYSTONES
    assert ledger.refreshes == 2 and ledger.skystones_remaining == 1


def test_refuses_to_start_below_gold_threshold():
    game = FakeGame(CONFIG, [shop(["Covenant"], [])])
    stop, ledger = run_game(game, gold=279_999)
    assert stop.kind is StopKind.OUT_OF_GOLD
    assert "Not enough gold to start" in stop.reason
    assert game.clicks == [] and ledger.entries == []


def test_stops_before_refresh_when_gold_below_most_expensive_item():
    game = FakeGame(CONFIG, [shop(["Covenant"], []), NOTHING])
    stop, ledger = run_game(game, gold=400_000)
    # 400,000 - 184,000 = 216,000 < 280,000: no refresh.
    assert stop.kind is StopKind.OUT_OF_GOLD
    assert ledger.refreshes == 0
    assert ledger.gold_remaining == 216_000


def test_stops_on_wanted_item_it_cannot_afford():
    game = FakeGame(CONFIG, [shop(["Covenant", "Mystic"], [])])
    stop, ledger = run_game(game, gold=300_000)
    # 300,000 - 184,000 = 116,000 left when the Mystic Medals come up.
    assert stop.kind is StopKind.OUT_OF_GOLD
    assert "Mystic Medals" in stop.reason and "116,000" in stop.reason
    assert [b[3] for b in game.bought] == ["covenant"]


def test_missing_buy_dialog_is_an_error_and_records_nothing():
    game = FakeGame(CONFIG, [shop(["Mystic"], [])], broken_buy_dialog=True)
    stop, ledger = run_game(game)
    assert stop.kind is StopKind.ERROR
    assert "buy dialog" in stop.reason
    assert ledger.entries == [] and game.bought == []
    assert game.debug_saves == ["buy_dialog"]


def test_missing_refresh_dialog_is_an_error():
    game = FakeGame(CONFIG, [NOTHING, NOTHING], broken_refresh_dialog=True)
    stop, ledger = run_game(game)
    assert stop.kind is StopKind.ERROR
    assert ledger.refreshes == 0
    assert game.debug_saves == ["refresh_dialog"]


def test_dry_run_clicks_nothing_scans_once_and_records_would_buys():
    game = DryRunFakeGame(CONFIG, [shop(["Covenant"], ["Mystic"]), NOTHING])
    ledger = Ledger(2_000_000, 30, dry_run=True)
    stop = run(game, CONFIG, ledger, 10, dry_run=True, sleep=no_sleep)
    assert stop.kind is StopKind.COMPLETED and "Dry run" in stop.reason
    assert game.bought == [] and game.round == 0
    assert ledger.purchases_by_item() == {"Covenant Bookmarks": 1, "Mystic Medals": 1}
    assert ledger.refreshes == 0


def test_user_stop_is_reported():
    class AbortingGame(FakeGame):
        def click(self, target):
            raise StopRun(StopKind.USER, "Stopped by the user")

    stop, _ = run_game(AbortingGame(CONFIG, [shop(["Covenant"], [])]))
    assert stop.kind is StopKind.USER


def test_unexpected_exception_becomes_error_stop():
    class CrashingGame(FakeGame):
        def read_text(self, region):
            raise RuntimeError("tesseract crashed")

    stop, _ = run_game(CrashingGame(CONFIG, [NOTHING]))
    assert stop.kind is StopKind.ERROR and "tesseract crashed" in stop.reason


def test_run_closes_the_ledger_file(tmp_path):
    path = tmp_path / "run.jsonl"
    ledger = Ledger(2_000_000, 30, path=path)
    run(FakeGame(CONFIG, [NOTHING]), CONFIG, ledger, 0, sleep=no_sleep)
    assert path.read_text().splitlines()[-1].startswith('{"type": "stop"')
