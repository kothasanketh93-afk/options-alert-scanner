"""
app/engines/news_intelligence/collector/scheduler.py -- optional
background periodic collection.
------------------------------------------------------------------------------
This module is OPT-IN -- NewsEngine.collect() (engine.py) can always be
called directly/on-demand (e.g. from a cron-job.org-triggered GitHub
Actions run, matching the project's own existing trigger pattern, see
scripts/main.py). NewsScheduler exists for callers who want an
in-process periodic loop instead (e.g. a long-running dashboard process).
It uses a plain daemon thread + threading.Event, not asyncio, so it works
identically whether the host process is sync or async -- an async host
can instead call engine.collect_async() directly on its own schedule and
skip this class entirely.
"""

from __future__ import annotations

import threading
from typing import Callable, Optional

from logging_setup import get_logger

_logger = get_logger("news_intelligence")


class NewsScheduler:
    def __init__(self, collect_fn: Callable[[], None], interval_seconds: int = 120):
        self._collect_fn = collect_fn
        self._interval_seconds = interval_seconds
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            _logger.warning("NEWS_SCHEDULER start() called while already running -- ignored")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, name="news-intel-scheduler", daemon=True)
        self._thread.start()
        _logger.info(f"NEWS_SCHEDULER started interval={self._interval_seconds}s")

    def stop(self, wait: bool = True, timeout: float = 5.0) -> None:
        self._stop_event.set()
        if wait and self._thread is not None:
            self._thread.join(timeout=timeout)
        _logger.info("NEWS_SCHEDULER stopped")

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._collect_fn()
            except Exception as exc:  # noqa: BLE001 -- one bad cycle must not kill the loop
                _logger.error(f"NEWS_SCHEDULER cycle raised: {exc}")
            self._stop_event.wait(self._interval_seconds)
