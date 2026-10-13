"""Command line commands: `run` and `inspect`."""

import argparse
import logging
from datetime import datetime
from pathlib import Path

from .config import Config
from .errors import StopRun
from .ocr import parse_number

log = logging.getLogger("e7shop")


def add_commands(commands: argparse._SubParsersAction, common: argparse.ArgumentParser) -> None:
    run = commands.add_parser("run", parents=[common], help="buy wanted items and refresh the shop")
    mode = run.add_mutually_exclusive_group(required=True)
    mode.add_argument("--count", type=int, metavar="N", help="refresh N times (capped by skystones)")
    mode.add_argument("--until-empty", action="store_true", help="refresh until out of skystones or gold")
    run.add_argument("--dry-run", action="store_true", help="read and move the mouse, but never click")

    commands.add_parser(
        "inspect", parents=[common], help="capture and OCR every configured region, to check the config"
    )


def confirm_value(label: str, value: int) -> int:
    while True:
        answer = input(f"{label}: {value:,}. Press Enter if that's right, or type the correct value: ").strip()
        if not answer:
            return value
        corrected = parse_number(answer)
        if corrected is not None:
            return corrected
        print("Please type a number.")


def cmd_run(args: argparse.Namespace, config: Config) -> int:
    from .abort import AbortSignal
    from .ledger import Ledger
    from .runner import check_screen, max_refreshes, preflight, resolve_refresh_count, run
    from .windows_game import WindowsGame

    abort = AbortSignal()
    unregister = abort.register_hotkey(config.abort_hotkey)
    game = None
    try:
        game = WindowsGame(config, abort, dry_run=args.dry_run)
        balances = preflight(game, config, sleep=abort.sleep)

        gold = confirm_value("Gold", balances.gold)
        skystones = confirm_value("Skystones", balances.skystones)
        maximum = max_refreshes(skystones, config.currency.refresh_cost)
        refreshes = resolve_refresh_count(None if args.until_empty else args.count, maximum)
        print(f"Refreshes: {refreshes} (max {maximum} with {skystones} skystones)")
        print(f"Press {config.abort_hotkey.upper()} at any time to stop.")
        input("Press Enter to start...")

        suffix = "-dry" if args.dry_run else ""
        ledger = Ledger(
            gold,
            skystones,
            path=Path(config.runs_dir) / f"{datetime.now():%Y%m%d-%H%M%S}{suffix}.jsonl",
            dry_run=args.dry_run,
        )
        # The prompts above took focus away from the game, and the screen may have changed.
        check_screen(game, config, sleep=abort.sleep)
        stop = run(game, config, ledger, refreshes, dry_run=args.dry_run, sleep=abort.sleep)
    except StopRun as stop_before_start:
        log.error(stop_before_start.reason)
        return 1
    finally:
        unregister()
        if game:
            game.close()

    print()
    print(f"Stopped: {stop.reason}")
    for line in ledger.summary_lines():
        print("  " + line)
    print(f"Run record: {ledger.path}")
    return 1 if stop.is_error else 0


def cmd_inspect(args: argparse.Namespace, config: Config) -> int:
    from .abort import AbortSignal
    from .windows_game import WindowsGame

    out_dir = Path(config.debug_dir) / f"inspect-{datetime.now():%Y%m%d-%H%M%S}"
    try:
        game = WindowsGame(config, AbortSignal())
        game.focus()
        size = game.client_size()
        print(f"Game window client area: {size[0]}x{size[1]} (config: {config.window_size.width}x{config.window_size.height})")
        results = game.inspect(out_dir)
        game.close()
    except StopRun as stop:
        log.error(stop.reason)
        return 1
    for label, text in results:
        print(f"{label:<28} {text}")
    print(f"\nCaptures saved to {out_dir}")
    print("Page 2 slots only make sense after scrolling: scroll in the game and run inspect again.")
    return 0


COMMANDS = {"run": cmd_run, "inspect": cmd_inspect}
