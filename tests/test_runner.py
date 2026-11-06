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


def test_preflight_accepts_any_size_of_the_same_shape():
    # The example layout is 1920x1080; the game scales its contents with its window.
    for size in ((1280, 720), (960, 540), (2560, 1440), (1281, 720)):
        game = FakeGame(CONFIG, [NOTHING], client_size=size)
        preflight(game, CONFIG, sleep=no_sleep)


def test_preflight_requires_the_same_shape():
    from e7shop.errors import WindowSizeMismatch

    game = FakeGame(CONFIG, [NOTHING], client_size=(1280, 1024))
    with pytest.raises(WindowSizeMismatch, match="1280x1024, a different shape"):
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


# Recovering when a dialog isn't recognised


def test_unrecognised_buy_dialog_over_the_button_is_confirmed():
    """The dialog opened but its text didn't read: the dimmed button shows it's there, so confirm it."""
    game = FakeGame(CONFIG, [shop(["Mystic"], [])], broken_buy_dialog=True)
    stop, ledger = run_game(game, refreshes=0)
    assert stop.kind is StopKind.COMPLETED
    assert [b[3] for b in game.bought] == ["mystic"] and ledger.purchases_by_item() == {"Mystic Medals": 1}
    assert game.debug_saves == ["buy_dialog"]


def test_buy_click_that_didnt_register_is_clicked_again():
    game = FakeGame(CONFIG, [shop(["Mystic"], [])])
    game.drop_clicks["buy"] = 1
    stop, ledger = run_game(game, refreshes=0)
    assert stop.kind is StopKind.COMPLETED and len(game.bought) == 1
    button = CONFIG.pages[0].slots[0].buy_button
    assert game.clicks[:2] == [button, button]


def test_buy_button_that_never_opens_the_dialog_stops_after_three_clicks():
    game = FakeGame(CONFIG, [shop(["Mystic"], [])])
    game.drop_clicks["buy"] = 99
    stop, ledger = run_game(game)
    assert stop.kind is StopKind.ERROR and "buy dialog" in stop.reason and "3 clicks" in stop.reason
    assert ledger.entries == [] and game.bought == []
    assert game.clicks.count(CONFIG.pages[0].slots[0].buy_button) == 3


def test_unrecognised_refresh_dialog_is_confirmed():
    game = FakeGame(CONFIG, [NOTHING, NOTHING], broken_refresh_dialog=True)
    stop, ledger = run_game(game, refreshes=1)
    assert stop.kind is StopKind.COMPLETED and ledger.refreshes == 1 and game.round == 1
    assert game.debug_saves == ["refresh_dialog"]


def test_refresh_click_that_didnt_register_is_clicked_again():
    game = FakeGame(CONFIG, [NOTHING, NOTHING])
    game.drop_clicks["refresh"] = 2
    stop, ledger = run_game(game, refreshes=1)
    assert stop.kind is StopKind.COMPLETED and ledger.refreshes == 1 and game.round == 1
    assert game.clicks.count(CONFIG.refresh_button) == 3


def test_refresh_button_that_never_opens_the_dialog_stops_after_three_clicks():
    game = FakeGame(CONFIG, [NOTHING, NOTHING])
    game.drop_clicks["refresh"] = 99
    stop, ledger = run_game(game)
    assert stop.kind is StopKind.ERROR and "refresh confirmation dialog" in stop.reason
    assert ledger.refreshes == 0


# Recovering when an item name doesn't read


def test_buy_dialog_left_open_is_confirmed_before_reading_on():
    """The buy confirm click didn't land: the dialog covers the next name, so it's confirmed first."""
    game = FakeGame(CONFIG, [shop(["Mystic", "-", "Covenant"], [])])
    game.drop_clicks["buy_confirm"] = 1
    stop, ledger = run_game(game, refreshes=0)
    assert stop.kind is StopKind.COMPLETED
    assert [b[3] for b in game.bought] == ["mystic", "covenant"]
    # Each purchase was recorded once, though the first needed a second confirm click.
    assert ledger.purchases_by_item() == {"Mystic Medals": 1, "Covenant Bookmarks": 1}
    assert ledger.skipped == 0


def test_refresh_dialog_left_open_is_confirmed_before_reading_on():
    game = FakeGame(CONFIG, [NOTHING, shop(["Covenant"], [])])
    game.drop_clicks["refresh_confirm"] = 1
    stop, ledger = run_game(game, refreshes=1)
    assert stop.kind is StopKind.COMPLETED
    assert ledger.refreshes == 1 and game.round == 1
    assert [b[3] for b in game.bought] == ["covenant"]


def test_name_that_reads_on_a_second_look_isnt_skipped():
    game = FakeGame(CONFIG, [shop(["Mystic"], [])])
    game.blank_name_reads = CONFIG.ocr.retries  # the whole first look comes back empty
    stop, ledger = run_game(game, refreshes=0)
    assert stop.kind is StopKind.COMPLETED and len(game.bought) == 1 and ledger.skipped == 0


def test_name_that_never_reads_is_skipped():
    game = FakeGame(CONFIG, [shop(["-", "  ", "Mystic"], [])])
    stop, ledger = run_game(game, refreshes=0)
    assert stop.kind is StopKind.COMPLETED
    assert [b[3] for b in game.bought] == ["mystic"]
    assert ledger.skipped == 1 and game.debug_saves == ["page1_slot2_name"]
    assert any("Skipped items:      1" in line for line in ledger.summary_lines())


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


def test_page_two_is_read_as_soon_as_the_list_stops_moving():
    game = FakeGame(CONFIG, [NOTHING])
    game.frames_moving_after_scroll = 3
    sleeps = []
    ledger = Ledger(2_000_000, 30)
    stop = run(game, CONFIG, ledger, 0, sleep=sleeps.append)
    assert stop.kind is StopKind.COMPLETED and game.scrolls == 1
    check = CONFIG.timings.settle_check_ms / 1000
    # Three looks still moving, then two that match: 4 short waits instead of a fixed one.
    assert sleeps.count(check) == 4


def test_list_still_moving_after_the_timeout_is_read_anyway(caplog):
    game = FakeGame(CONFIG, [shop([], ["Mystic"])])
    game.frames_moving_after_scroll = 1000
    stop, _ = run_game(game, refreshes=0)
    assert stop.kind is StopKind.COMPLETED and len(game.bought) == 1
    assert "still moving" in caplog.text


def test_wait_until_still_with_no_timeout_doesnt_look():
    from e7shop.geometry import Rect
    from e7shop.shop import wait_until_still

    game = FakeGame(CONFIG, [NOTHING])
    assert wait_until_still(game, Rect(0, 0, 10, 10), interval_s=0.08, timeout_s=0, sleep=no_sleep)
    assert game.grabs == 0


# Items already bought (a refresh that didn't go through)


def test_failed_refresh_leaves_bought_items_greyed_out_and_they_are_skipped():
    """The incident from the first long run: a refresh didn't go through, so the next round saw the
    Covenant Bookmarks it had just bought. It used to click it 3 times and stop with an error."""
    game = FakeGame(CONFIG, [shop([], ["Covenant"]), NOTHING])
    game.failed_refreshes = 1
    stop, ledger = run_game(game, refreshes=2)
    assert stop.kind is StopKind.COMPLETED
    assert [b[3] for b in game.bought] == ["covenant"]
    assert ledger.sold_out == 1 and ledger.purchases_by_item() == {"Covenant Bookmarks": 1}
    # The greyed-out button was never clicked again.
    assert game.clicks.count(CONFIG.pages[1].slots[0].buy_button) == 1
    assert any("Already bought:     1" in line for line in ledger.summary_lines())


def test_sold_out_check_comes_before_the_gold_check():
    """A greyed-out wanted item with too little gold left must not end the run as out of gold."""
    game = FakeGame(CONFIG, [shop(["Mystic"], [])])
    game.sold.add((0, 0, 0))
    stop, ledger = run_game(game, gold=300_000, refreshes=0)
    assert stop.kind is StopKind.COMPLETED and ledger.sold_out == 1 and game.clicks == []


def test_green_dominance_separates_available_from_bought_buttons():
    from PIL import Image

    from e7shop.capture import green_dominance
    from e7shop.shop import SOLD_OUT_MAX_GREEN

    # Mean colours measured on the real shop's "1/1 Buy" and "0/1 Buy" buttons.
    available = Image.new("RGB", (40, 20), (31, 64, 36))
    bought = Image.new("RGB", (40, 20), (21, 32, 23))
    assert green_dominance(available) > SOLD_OUT_MAX_GREEN > green_dominance(bought)


def test_dialog_fading_out_before_a_refresh_isnt_mistaken_for_an_open_dialog():
    """The false refresh from the first long run: the Refresh button's "before" look was taken while
    the buy dialog was still fading out. The refresh click didn't register, the next look was
    brighter, and "different" was taken as "covered", so a refresh that never happened was confirmed."""
    # As in the incident, the item is the last one on page 2, so the refresh follows the purchase.
    game = FakeGame(CONFIG, [shop([], ["-", "Covenant"]), NOTHING])
    game.fade_after_buy = 2
    game.drop_clicks["refresh"] = 1
    stop, ledger = run_game(game, refreshes=1)
    assert stop.kind is StopKind.COMPLETED
    # The missed click was retried, and the refresh really happened.
    assert game.clicks.count(CONFIG.refresh_button) == 2
    assert ledger.refreshes == 1 and game.round == 1


def test_sold_out_check_waits_for_a_fading_dialog():
    """Right after a purchase the shop is dimmed for a moment: a green button must not look sold out."""
    game = FakeGame(CONFIG, [shop(["Covenant", "Mystic"], [])])
    game.fade_after_buy = 2
    stop, ledger = run_game(game, refreshes=0)
    assert stop.kind is StopKind.COMPLETED
    assert [b[3] for b in game.bought] == ["covenant", "mystic"] and ledger.sold_out == 0
