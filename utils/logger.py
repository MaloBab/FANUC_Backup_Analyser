"""Utilitaire de configuration du logging applicatif."""

from __future__ import annotations
import logging
import logging.handlers
from pathlib import Path

LOG_DIR = Path.home() / ".fanuc_extractor" / "logs"
LOG_FILE = LOG_DIR / "app.log"


def setup_logger(level: int = logging.DEBUG) -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(level)

    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    root.addHandler(ch)

    fh = logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=2 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    root.addHandler(fh)

    return root