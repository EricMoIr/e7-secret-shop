"""Entry point. With no command, opens the GUI.

    python -m e7shop [--config config.json]                      # GUI
    python -m e7shop run --config config.json --count 10 [--dry-run]
    python -m e7shop run --config config.json --until-empty
    python -m e7shop inspect --config config.json
"""

import argparse
import logging
import os
import sys

from pydantic import ValidationError

from .config import load_config
from .logs import setup_logging

log = logging.getLogger("e7shop")


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    from . import cli

    # SUPPRESS defaults: these options are accepted before or after the command, and a subcommand's
    # defaults would otherwise overwrite values given before it.
    common = argparse.ArgumentParser(add_help=False, argument_default=argparse.SUPPRESS)
    common.add_argument("--config", help="path to the config file (default: config.json)")
    common.add_argument("--elevated", action="store_true", help=argparse.SUPPRESS)
    common.add_argument("--cwd", help=argparse.SUPPRESS)

    parser = argparse.ArgumentParser(prog="e7shop", description="Epic Seven Secret Shop helper", parents=[common])
    commands = parser.add_subparsers(dest="command", help="omit to open the GUI")
    cli.add_commands(commands, common)
    args = parser.parse_args(argv)
    # Not parser.set_defaults: that would also change the actions the subcommands share.
    for name, default in (("config", None), ("elevated", False), ("cwd", None)):
        if not hasattr(args, name):
            setattr(args, name, default)
    return args


def run_cli(args: argparse.Namespace) -> int:
    from . import cli

    args.config = args.config or "config.json"
    try:
        config = load_config(args.config)
    except FileNotFoundError:
        print(f"Config file not found: {args.config}", file=sys.stderr)
        return 2
    except (ValidationError, ValueError) as exc:
        print(f"Invalid config {args.config}:\n{exc}", file=sys.stderr)
        return 2

    log_path = setup_logging(config.logs_dir, extra_handlers=[logging.StreamHandler()])
    log.debug("Log file: %s", log_path)
    return cli.COMMANDS[args.command](args, config)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.cwd:
        os.chdir(args.cwd)
    gui = args.command is None
    try:
        if sys.platform != "win32":
            print("e7shop runs on Windows only (use Windows Python, not WSL).", file=sys.stderr)
            return 2

        from . import elevation

        if not elevation.is_admin():
            if elevation.relaunch_as_admin(gui=gui):
                return 0
            print("e7shop needs administrator rights.", file=sys.stderr)
            return 1

        if gui:
            # Qt makes the process per-monitor DPI aware itself.
            from .gui.app import run_gui

            return run_gui(args.config)

        from .window import enable_dpi_awareness

        enable_dpi_awareness()
        return run_cli(args)
    finally:
        if args.elevated and not gui:
            input("\nPress Enter to close...")


if __name__ == "__main__":
    sys.exit(main())
