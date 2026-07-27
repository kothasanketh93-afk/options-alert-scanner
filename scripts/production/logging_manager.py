"""
production/logging_manager.py
------------------------------
Part 9, item 1: Centralized Logging.

Relationship to the existing scripts/logging_setup.py
------------------------------------------------------
logging_setup.py is untouched and still works exactly as before: it
writes structured, leveled log lines to STDOUT, which is the right
choice for the scanner/tracker/heartbeat scripts that run as one-shot
jobs inside ephemeral GitHub Actions containers (see that file's own
docstring for why -- disk files there vanish with the container).

This module is for the OTHER half of the system that Part 9 is aimed
at: long-running processes (the dashboard API, a persistent worker,
a VM/container deployment) where disk *does* persist across restarts
and rotating log files are actually useful. It does not replace
get_logger() from logging_setup.py -- it adds a second, opt-in logger
factory (`get_production_logger`) with:

- one rotating file per named channel (see CHANNELS below)
- daily rotation AND a max-size cap (whichever comes first)
- automatic gzip archive of rotated files
- automatic deletion of archives older than LOG_RETENTION_DAYS
- the same "DEBUG/INFO/WARNING/ERROR/CRITICAL" leveled, structured
  format logging_setup.py already uses, so log lines look the same
  whether they end up on stdout (GH Actions) or in a file (dashboard)

Nothing here changes what already-working code logs or how -- it is
purely a new, separate way to get a logger, used only where a caller
explicitly asks for it.
"""

from __future__ import annotations

import glob
import gzip
import logging
import logging.handlers
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict

# The dedicated channels Part 9 asks for. Any other channel name still
# works (get_production_logger("whatever")) -- these are just the ones
# with a defined home in LOG_DIR.
CHANNELS = (
    "scanner",
    "decision_engine",
    "replay",
    "analytics",
    "health",
    "dashboard",
    "api",
    "broker",
    "websocket",
    "errors",
    "performance",
)

DEFAULT_LOG_DIR = os.environ.get("PRODUCTION_LOG_DIR", "logs")
DEFAULT_MAX_BYTES = int(os.environ.get("PRODUCTION_LOG_MAX_BYTES", 10 * 1024 * 1024))  # 10 MB
DEFAULT_BACKUP_COUNT = int(os.environ.get("PRODUCTION_LOG_BACKUP_COUNT", 14))
DEFAULT_RETENTION_DAYS = int(os.environ.get("PRODUCTION_LOG_RETENTION_DAYS", 30))

_LOG_FORMAT = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


@dataclass(frozen=True)
class LoggingConfig:
    """All knobs for the production logging system in one place, so
    there's exactly one thing to change (not scattered constants)."""

    log_dir: str = DEFAULT_LOG_DIR
    max_bytes: int = DEFAULT_MAX_BYTES
    backup_count: int = DEFAULT_BACKUP_COUNT
    retention_days: int = DEFAULT_RETENTION_DAYS
    level: int = logging.INFO
    also_log_to_stdout: bool = True


class _GzipRotator:
    """Handler `rotator` hook: after RotatingFileHandler/TimedRotating
    Handler renames foo.log -> foo.log.1, gzip it and drop the raw
    copy. Keeps rotated logs small on disk without a separate cron job."""

    def __call__(self, source: str, dest: str) -> None:
        if not os.path.exists(source):
            return
        gz_dest = dest if dest.endswith(".gz") else dest + ".gz"
        with open(source, "rb") as f_in, gzip.open(gz_dest, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
        os.remove(source)
        if dest != gz_dest and os.path.exists(dest):
            os.remove(dest)


class _SizeAndTimeRotatingHandler(logging.handlers.TimedRotatingFileHandler):
    """Rotates on the earlier of: midnight (daily), or max_bytes
    exceeded -- satisfying both "daily log rotation" and "maximum log
    size" from the Part 9 spec with one handler instead of stacking two
    (stacking two RotatingFileHandlers on the same file races on
    rollover). Archival to .gz is done via the `rotator` hook."""

    def __init__(self, filename: str, max_bytes: int, backup_count: int):
        super().__init__(filename, when="midnight", backupCount=backup_count, encoding="utf-8")
        self._max_bytes = max_bytes
        self.rotator = _GzipRotator()

    def shouldRollover(self, record: logging.LogRecord) -> int:  # noqa: N802 (stdlib override)
        if super().shouldRollover(record):
            return 1
        if self._max_bytes <= 0:
            return 0
        try:
            if self.stream is None:
                self.stream = self._open()
            if self.stream.tell() >= self._max_bytes:
                return 1
        except OSError:
            pass
        return 0


class LoggingManager:
    """Owns one rotating, archiving file handler per channel plus an
    optional shared stdout handler. Safe to construct more than once
    (e.g. in tests) -- re-configuring a channel just replaces its
    handler rather than stacking duplicate handlers, which is the
    actual bug this class exists to prevent: naive
    `logging.getLogger(x).addHandler(...)` calls scattered across
    modules eventually duplicate every log line N times over."""

    def __init__(self, config: LoggingConfig | None = None):
        self.config = config or LoggingConfig()
        self._loggers: Dict[str, logging.Logger] = {}
        Path(self.config.log_dir).mkdir(parents=True, exist_ok=True)

    def get_logger(self, channel: str) -> logging.Logger:
        if channel in self._loggers:
            return self._loggers[channel]

        logger = logging.getLogger(f"production.{channel}")
        logger.setLevel(self.config.level)
        logger.propagate = False
        # Idempotent: clear any handlers a prior get_logger() call (or
        # a stray basicConfig elsewhere) left attached, so we never
        # double-log.
        for h in list(logger.handlers):
            logger.removeHandler(h)

        formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)

        file_path = os.path.join(self.config.log_dir, f"{channel}.log")
        file_handler = _SizeAndTimeRotatingHandler(
            file_path, max_bytes=self.config.max_bytes, backup_count=self.config.backup_count
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

        if self.config.also_log_to_stdout:
            stream_handler = logging.StreamHandler()
            stream_handler.setFormatter(formatter)
            logger.addHandler(stream_handler)

        self._loggers[channel] = logger
        return logger

    def cleanup_old_logs(self) -> int:
        """Delete archived (.gz) logs older than retention_days.
        Returns the count deleted. Call this periodically (e.g. from
        the background task manager's daily housekeeping job) or once
        at startup."""
        cutoff = time.time() - self.config.retention_days * 86400
        deleted = 0
        for path in glob.glob(os.path.join(self.config.log_dir, "*.gz")):
            try:
                if os.path.getmtime(path) < cutoff:
                    os.remove(path)
                    deleted += 1
            except OSError:
                continue
        return deleted


_default_manager: LoggingManager | None = None


def _get_default_manager() -> LoggingManager:
    global _default_manager
    if _default_manager is None:
        _default_manager = LoggingManager()
    return _default_manager


def get_production_logger(channel: str) -> logging.Logger:
    """Drop-in equivalent to logging_setup.get_logger(channel), but
    backed by a rotating, archiving file per channel instead of
    stdout-only. Use this for long-running processes (dashboard API,
    a persistent worker); use logging_setup.get_logger() unchanged for
    the existing ephemeral GH Actions scripts."""
    return _get_default_manager().get_logger(channel)


def cleanup_old_logs() -> int:
    return _get_default_manager().cleanup_old_logs()


def configure(config: LoggingConfig) -> LoggingManager:
    """Explicitly (re)configure the default manager, e.g. at process
    startup with settings read from config.py. Optional -- if never
    called, get_production_logger() lazily builds one with defaults."""
    global _default_manager
    _default_manager = LoggingManager(config)
    return _default_manager
