"""
error_handling.py
------------------
Part 10 -- Fault tolerance / structured error handling for the Phase 3
poll-and-diff pipeline (app/backend/services/live_service.py).

WHY THIS FILE WAS ADDED (see Part 10's Modification Policy -- every
change must state why a modification/addition was required):

Before this, live_service.compute_diff_events() ran four independent
reads -- scanner_status, system_health, trades, replay_progress --
with NO error isolation between them. An exception raised by any one
of them (e.g. a transient git-pull failure inside replay_service, or
psutil misbehaving in health_service) aborted the *entire* cycle
before the other three ever ran. poll_loop()'s existing
try/except-log-continue only caught this AFTER the fact, once per
whole cycle, so a perfectly healthy scanner_status update would
silently never reach connected dashboard clients just because one
unrelated source hiccuped that tick.

This module is purely additive: it does not change what any existing
service returns, does not touch main.py, does not touch scanner or
broker logic, and does not change the message schema clients receive
on /ws/dashboard. It only lets live_service isolate one source's
failure from the others and keep serving everything that still works.

Contents:
  - safe_call()               -- run one data-source read; isolate
                                   its failure; never raises; returns
                                   a structured error record on
                                   failure (timestamp, component,
                                   module, method, exception type,
                                   message, stack trace, suggested
                                   resolution, recovery action -- the
                                   fields Part 10's "Error Handling"
                                   section calls for).
  - ComponentFailureTracker    -- thread-safe consecutive-failure
                                   counter per component, used by
                                   poll_loop for graceful interval
                                   backoff and available as an
                                   observability hook.
"""

import logging
import threading
import traceback
from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))

log = logging.getLogger("trading_system.backend.error_handling")


def _now_ist_iso() -> str:
    return datetime.now(IST).isoformat()


def build_error_record(
    component: str,
    module: str,
    method: str,
    exc: Exception,
    suggested_resolution: str = None,
    recovery_action: str = None,
) -> dict:
    """Structured error record matching Part 10's Error Handling spec."""
    return {
        "timestamp": _now_ist_iso(),
        "component": component,
        "module": module,
        "method": method,
        "exception_type": type(exc).__name__,
        "message": str(exc),
        "stack_trace": traceback.format_exc(),
        "suggested_resolution": suggested_resolution or (
            "check logs for this component; verify its upstream "
            "dependency (state branch / broker / network) is reachable"
        ),
        "recovery_action": recovery_action or (
            "this cycle's update for this component was skipped; the "
            "previous known-good value is kept and it will retry "
            "automatically on the next poll cycle"
        ),
    }


def safe_call(
    component: str,
    module: str,
    method: str,
    func,
    *args,
    suggested_resolution: str = None,
    **kwargs,
):
    """
    Runs func(*args, **kwargs).
    Success -> (result, None).
    Failure -> logs a structured error record and returns (None, record)
               instead of raising, so the caller can isolate this one
               source's failure and continue with everything else.

    Deliberately catches bare Exception (not just specific types):
    every call site here reads a downstream service (scanner_service,
    health_service, replay_service) whose own failure modes are not
    this module's concern -- the whole point is "whatever went wrong,
    don't let it take the other three sources down with it."
    """
    try:
        return func(*args, **kwargs), None
    except Exception as exc:  # noqa: BLE001 -- intentional: isolate this source's failure from the rest of the poll cycle
        record = build_error_record(
            component, module, method, exc, suggested_resolution=suggested_resolution
        )
        log.error(
            "component=%s module=%s method=%s exception=%s message=%s -- %s",
            component, module, method, record["exception_type"], record["message"],
            record["recovery_action"],
        )
        log.debug("stack trace for %s.%s:\n%s", module, method, record["stack_trace"])
        return None, record


class ComponentFailureTracker:
    """
    Thread-safe consecutive-failure counter per component.

    Used for graceful degradation: poll_loop widens its polling
    interval only once a component has failed repeatedly (not on the
    first blip), and narrows it back automatically the moment that
    component succeeds again. This is intentionally NOT a hard
    circuit breaker that stops calling a failing source -- these are
    read-only monitoring reads, so "stop trying" would only mean
    staler data with no corresponding benefit (no external system is
    being protected from load the way a broker circuit breaker
    protects a broker API).
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._consecutive_failures: dict[str, int] = {}
        self._last_error: dict[str, dict] = {}

    def record_success(self, component: str) -> None:
        with self._lock:
            self._consecutive_failures[component] = 0
            self._last_error.pop(component, None)

    def record_failure(self, component: str, error_record: dict) -> int:
        """Returns the new consecutive-failure count for this component."""
        with self._lock:
            n = self._consecutive_failures.get(component, 0) + 1
            self._consecutive_failures[component] = n
            self._last_error[component] = error_record
            return n

    def worst_streak(self) -> int:
        """Highest current consecutive-failure count across all components."""
        with self._lock:
            return max(self._consecutive_failures.values(), default=0)

    def get_status(self) -> dict:
        """
        Snapshot for observability -- e.g. a future /api/health
        extension or dashboard panel. NOT currently read by
        health_service.py: wiring it into health_service's response
        shape would be a second, separate modification with its own
        backward-compatibility considerations for existing API
        consumers, so this pass only adds the hook and leaves that
        integration decision for whoever builds it (see README.md
        Part 10 notes).
        """
        with self._lock:
            return {
                "consecutive_failures": dict(self._consecutive_failures),
                "last_errors": dict(self._last_error),
            }
