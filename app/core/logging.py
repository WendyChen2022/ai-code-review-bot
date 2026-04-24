"""Logging configuration for the application.

All modules should import `logger` from here instead of using print().
This gives us timestamps, log levels, and a single place to change the
format or add handlers later (e.g. file logging, JSON output).
"""

import logging
import sys


def _setup() -> logging.Logger:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    return logging.getLogger("review-bot")


# Single shared logger — import this everywhere.
logger = _setup()
