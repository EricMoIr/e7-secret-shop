"""Command line entry point (phase 1). The GUI replaces this later.

    python -m e7shop run --config config.json --count 10 [--dry-run]
    python -m e7shop run --config config.json --until-empty
    python -m e7shop inspect --config config.json
"""

import argparse
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from .config import Config, load_config
from .errors import StopRun

log = logging.getLogger("e7shop")


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default="config.json", help="path to the config file (default: config.json)")
    common.add_argument("--elevated", action="store_true", help=argparse.SUPPRESS)
    common.add_argument("--cwd", help=argparse.SUPPRESS)

    parser = argparse.ArgumentParser(prog="e7shop", description="Epic Seven Secret Shop helper")
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", parents=[common], help="buy wanted items and refresh the shop")
    mode = run.add_mutually_exclusive_group(required=True)
    mode.add_argument("--count", type=int, metavar="N", help="refresh N times (capped by skystones)")
    mode.add_argument("--until-empty", action="store_true", help="refresh until out of skystones or gold")
    run.add_argument("--dry-run", action="store_true", help="read and move the mouse, but never click")

    commands.add_parser(
        "inspect", parents=[common], help="capture and OCR every configured region, to check the config"
    )
    return parser.parse_args(argv)


def setup_logging(logs_dir: str) -> Path:
    path = Path(logs_dir) / f"{datetime.now():%Y%m%d-%H%M%S}.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(path, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
    log.setLevel(logging.DEBUG)
    log.addHandler(file_handler)
    log.addHandler(console)
    return path


def confirm_value(label: str, value: int) -> int:
    from .ocr import parse_number

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
    from .runner import max_refreshes, preflight, resolve_refresh_count, run
    from .windows_game import WindowsGame

    abort = AbortSignal()
    unregister = abort.register_hotkey(config.abort_hotkey)
    game = None
    try:
        game = WindowsGame(config, abort, dry_run=args.dry_run)
        balances = preflight(game, config)

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
        # The prompts above took focus away from the game.
        game.focus()
        time.sleep(config.timings.after_focus_ms / 1000)
        stop = run(game, config, ledger, refreshes, dry_run=args.dry_run)
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
        time.sleep(config.timings.after_focus_ms / 1000)
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


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.cwd:
        os.chdir(args.cwd)
    try:
        if sys.platform != "win32":
            print("e7shop runs on Windows only (use Windows Python, not WSL).", file=sys.stderr)
            return 2

        from . import elevation
        from .window import enable_dpi_awareness

        if not elevation.is_admin():
            if elevation.relaunch_as_admin():
                return 0
            print("e7shop needs administrator rights.", file=sys.stderr)
            return 1
        enable_dpi_awareness()

        try:
            config = load_config(args.config)
        except FileNotFoundError:
            print(f"Config file not found: {args.config}", file=sys.stderr)
            return 2
        except (ValidationError, ValueError) as exc:
            print(f"Invalid config {args.config}:\n{exc}", file=sys.stderr)
            return 2

        log_path = setup_logging(config.logs_dir)
        log.debug("Log file: %s", log_path)
        return {"run": cmd_run, "inspect": cmd_inspect}[args.command](args, config)
    finally:
        if args.elevated:
            input("\nPress Enter to close...")


if __name__ == "__main__":
    sys.exit(main())
