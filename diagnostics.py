"""Persistent, privacy-conscious diagnostics for the desktop application."""

import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys


def _data_root():
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local"
        return Path(base) / "DeYaz"
    base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share"
    return Path(base) / "deyaz"


LOG_DIR = _data_root() / "logs"
LOG_FILE = LOG_DIR / "deyaz.log"


def setup_logging():
    """Write bounded diagnostic logs without recording keys or transcript text."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger("deyaz")
    if root.handlers:
        return LOG_FILE
    root.setLevel(logging.INFO)
    handler = RotatingFileHandler(
        LOG_FILE, maxBytes=2 * 1024 * 1024, backupCount=4, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        "%Y-%m-%d %H:%M:%S",
    ))
    root.addHandler(handler)
    root.propagate = False
    return LOG_FILE


def install_exception_hook():
    previous = sys.excepthook
    previous_thread = getattr(__import__("threading"), "excepthook", None)

    def report(exc_type, exc, traceback):
        logging.getLogger("deyaz.crash").critical(
            "Unhandled exception", exc_info=(exc_type, exc, traceback)
        )
        previous(exc_type, exc, traceback)

    sys.excepthook = report

    if previous_thread is not None:
        import threading

        def report_thread(args):
            logging.getLogger("deyaz.crash").critical(
                "Unhandled thread exception thread=%s",
                getattr(args.thread, "name", "unknown"),
                exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
            )
            previous_thread(args)

        threading.excepthook = report_thread
