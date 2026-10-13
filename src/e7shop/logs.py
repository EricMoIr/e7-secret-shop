"""Logging setup shared by the CLI and the GUI."""

import logging
from datetime import datetime
from pathlib import Path

log = logging.getLogger("e7shop")


def setup_logging(logs_dir: str, *, extra_handlers: list[logging.Handler] = ()) -> Path:
    """Log DEBUG and up to a new file in `logs_dir`, plus INFO and up to `extra_handlers`.

    Replaces any handlers from a previous call, so the GUI can call it again when the config changes.
    """
    for handler in list(log.handlers):
        log.removeHandler(handler)
        if isinstance(handler, logging.FileHandler):
            handler.close()

    path = Path(logs_dir) / f"{datetime.now():%Y%m%d-%H%M%S}.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(path, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    log.addHandler(file_handler)

    for handler in extra_handlers:
        handler.setLevel(logging.INFO)
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
        log.addHandler(handler)

    log.setLevel(logging.DEBUG)
    return path
