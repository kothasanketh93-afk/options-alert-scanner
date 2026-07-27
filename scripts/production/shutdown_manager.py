"""
production/shutdown_manager.py
-------------------------------
Part 9, item 13: Shutdown Handling.

Coordinates a clean shutdown on CTRL+C (SIGINT), SIGTERM (system
restart / orchestrator stop / `systemctl stop`), or an explicit
shutdown API call -- so cache gets saved, files/handles get closed,
WebSocket connections get closed, background workers get stopped, and
broker sessions get closed, in that order, before the process exits.

This is purely additive infrastructure: nothing in the existing
scanner/decision-engine/replay/analytics/dashboard code is required to
change. A caller registers cleanup callbacks; nothing happens unless
something calls register() or install_signal_handlers().

Usage
-----
    from production.shutdown_manager import shutdown_manager

    shutdown_manager.register("save_cache", cache.flush_to_disk)
    shutdown_manager.register("close_broker", broker.close_session)
    shutdown_manager.install_signal_handlers()   # SIGINT + SIGTERM

    ... run the app ...

    # Or trigger manually, e.g. from a dashboard "/shutdown" API route:
    shutdown_manager.shutdown(reason="api_request")
"""

from __future__ import annotations

import atexit
import logging
import signal
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional

try:
    from logging_setup import get_logger
    _logger = get_logger("main")
except Exception:  # pragma: no cover - defensive: never let logging break shutdown
    _logger = logging.getLogger("shutdown_manager")


@dataclass
class _Hook:
    name: str
    fn: Callable[[], None]
    timeout: float = 10.0


@dataclass
class ShutdownReport:
    reason: str
    started_at: float
    finished_at: Optional[float] = None
    completed: List[str] = field(default_factory=list)
    failed: List[str] = field(default_factory=list)

    @property
    def duration_seconds(self) -> Optional[float]:
        if self.finished_at is None:
            return None
        return round(self.finished_at - self.started_at, 3)


class ShutdownManager:
    """Runs registered hooks in reverse-registration order (last
    registered = first shut down, mirroring normal resource-teardown
    convention: the thing that depends on something else shuts down
    before the thing it depends on)."""

    def __init__(self):
        self._hooks: List[_Hook] = []
        self._lock = threading.Lock()
        self._shutting_down = False
        self._last_report: Optional[ShutdownReport] = None
        self._signal_handlers_installed = False

    def register(self, name: str, fn: Callable[[], None], timeout: float = 10.0) -> None:
        """Register a cleanup callback. `fn` must take no arguments and
        should be safe to call even if the resource it cleans up was
        never fully initialized (check for None internally)."""
        with self._lock:
            self._hooks.append(_Hook(name=name, fn=fn, timeout=timeout))

    def install_signal_handlers(self) -> None:
        """Install SIGINT (CTRL+C) and SIGTERM handlers that trigger a
        graceful shutdown instead of an abrupt kill. Safe to call more
        than once. Must be called from the main thread (a Python
        stdlib `signal` requirement, not specific to this code)."""
        if self._signal_handlers_installed:
            return

        def _handler(signum, _frame):
            sig_name = signal.Signals(signum).name
            _logger.warning("Received %s -- starting graceful shutdown.", sig_name)
            self.shutdown(reason=sig_name)

        signal.signal(signal.SIGINT, _handler)
        signal.signal(signal.SIGTERM, _handler)
        atexit.register(self._atexit_fallback)
        self._signal_handlers_installed = True

    def _atexit_fallback(self) -> None:
        # Covers "unexpected stop" (e.g. an uncaught exception
        # propagating out of main) where no signal was delivered but
        # the interpreter is exiting anyway -- last chance to flush
        # cache / close sessions.
        if not self._shutting_down:
            self.shutdown(reason="atexit")

    def shutdown(self, reason: str = "manual") -> ShutdownReport:
        """Idempotent: a second call while shutdown is already in
        progress (or already finished) returns the existing report
        instead of running hooks twice."""
        with self._lock:
            if self._shutting_down:
                return self._last_report  # type: ignore[return-value]
            self._shutting_down = True
            hooks = list(reversed(self._hooks))

        report = ShutdownReport(reason=reason, started_at=time.time())
        for hook in hooks:
            try:
                _run_with_timeout(hook.fn, hook.timeout)
                report.completed.append(hook.name)
                _logger.info("Shutdown hook '%s' completed.", hook.name)
            except Exception as exc:  # noqa: BLE001 - a failing hook must not block the rest
                report.failed.append(hook.name)
                _logger.error("Shutdown hook '%s' failed: %s", hook.name, exc)
        report.finished_at = time.time()
        self._last_report = report
        _logger.warning(
            "Shutdown complete (reason=%s, %d/%d hooks ok, %.2fs).",
            reason, len(report.completed), len(hooks), report.duration_seconds or 0.0,
        )
        return report

    @property
    def is_shutting_down(self) -> bool:
        return self._shutting_down

    @property
    def last_report(self) -> Optional[ShutdownReport]:
        return self._last_report


def _run_with_timeout(fn: Callable[[], None], timeout: float) -> None:
    error: dict = {}

    def _target():
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - re-raised on the calling thread
            error["exc"] = exc

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise TimeoutError(f"shutdown hook did not complete within {timeout}s")
    if "exc" in error:
        raise error["exc"]


# Process-wide instance. Import this rather than constructing your own
# unless you specifically need isolated hook state (e.g. per-test).
shutdown_manager = ShutdownManager()
