"""
utils/logging_utils.py -- Logging configuration, isolated from the host app
UNLESS the host's own logger factory is importable, in which case that
one is reused instead (see the merge/integration note below).

------------------------------------------------------------------------------
MERGE NOTE (Part 11 -> Part 10 integration):

This module was written so quant_intelligence stays a fully independent,
standalone package (importable and testable on its own, per the Part 11
spec, with its own dedicated "quant_intelligence" logger and handler).

When this package is vendored inside the host NSE Options Scanner at
scripts/app/engines/quant_intelligence/ (scripts/ is on sys.path -- see
main.py's `sys.path.insert(0, os.path.dirname(__file__))`), the host's
own scripts/logging_setup.get_logger() becomes importable. Per the
merge spec's "reuse the existing logging system, no duplicate loggers,
no duplicate handlers, use existing logger factory" requirement,
get_logger() below prefers that host factory when it's importable --
so in the merged application there is exactly ONE handler on ONE root
logger ("trading_system", configured by logging_setup.py), and
quant_intelligence's log lines appear as channel
"trading_system.quant_intelligence[.<name>]" alongside "scanner",
"tracker", "decision_engine", etc.

Only when scripts/logging_setup is NOT importable (i.e. this package
is used completely standalone, outside the host app -- e.g. running
its own tests via run_tests.py from a checkout that doesn't have the
host's scripts/ on sys.path) does it fall back to configuring its own
dedicated "quant_intelligence" logger + handler, exactly as before.
Nothing about the module's public API (`get_logger(name=None)`)
changed -- every other file in this package still just calls
`get_logger("some_name")` and never needs to know which branch fired.
"""

from __future__ import annotations

import logging
import threading
from typing import Optional

_LOCK = threading.Lock()
_CONFIGURED = False
_HOST_FACTORY = None  # set on first use: the host's get_logger, or False if unavailable

LOGGER_NAME = "quant_intelligence"


def _resolve_host_factory():
    """Import scripts/logging_setup.get_logger exactly once, if it's on
    sys.path (true whenever this package runs inside the merged host
    app). Returns None if unavailable -- never raises."""
    global _HOST_FACTORY
    if _HOST_FACTORY is not None:
        return _HOST_FACTORY or None
    try:
        from logging_setup import get_logger as host_get_logger  # host factory, scripts/logging_setup.py
        _HOST_FACTORY = host_get_logger
    except ImportError:
        _HOST_FACTORY = False
    return _HOST_FACTORY or None


def configure_logging(level: str = "INFO", *, fmt: Optional[str] = None) -> logging.Logger:
    """Idempotently configure and return the module's dedicated logger.
    No-op (and never called) when the host factory is in use -- the
    host's logging_setup.py already configured its own single handler."""
    global _CONFIGURED
    logger = logging.getLogger(LOGGER_NAME)
    with _LOCK:
        if not _CONFIGURED:
            handler = logging.StreamHandler()
            handler.setFormatter(
                logging.Formatter(
                    fmt or "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
                )
            )
            logger.addHandler(handler)
            logger.propagate = False  # never bleed into the host app's root logger
            _CONFIGURED = True
        logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    return logger


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """Get a child logger under the module namespace.

    Inside the merged host app: delegates to scripts/logging_setup.get_logger
    (channel "trading_system.quant_intelligence[.<name>]") -- reuses the
    host's single handler, no duplicate logger tree.

    Standalone: configures (once) and returns this package's own
    dedicated "quant_intelligence[.<name>]" logger + handler, unchanged
    from the original independent-module behavior.
    """
    host_factory = _resolve_host_factory()
    if host_factory is not None:
        channel = f"{LOGGER_NAME}.{name}" if name else LOGGER_NAME
        return host_factory(channel)

    configure_logging()
    if name:
        return logging.getLogger(f"{LOGGER_NAME}.{name}")
    return logging.getLogger(LOGGER_NAME)
