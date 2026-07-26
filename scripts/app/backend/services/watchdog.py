"""
watchdog.py
-----------
Part 10 -- Watchdog for the Phase 3 poll-and-diff background task
(live_service.poll_loop).

WHY THIS WAS ADDED: poll_loop() awaits
asyncio.to_thread(compute_diff_events) every cycle. compute_diff_events()
already isolates each of its four sources (see error_handling.py), but
it still calls state_reader, which can perform a blocking git
operation. A genuinely hung git process (network half-open, waiting on
a credential prompt that will never come, etc.) would leave that
thread blocked indefinitely -- nothing in the existing code timed that
out, so poll_loop would sit awaiting a future that never resolves, and
nothing else in the app would ever notice.

This module is a small, focused watchdog for exactly that one
background task:
  - CycleWatchdog records when each poll cycle starts/finishes, how
    long it took, and how many times a cycle has timed out.
  - live_service.poll_loop wraps its asyncio.to_thread(...) call in
    asyncio.wait_for(..., timeout=settings.WS_CYCLE_TIMEOUT_SECONDS),
    so a hung cycle can no longer block the loop forever -- it is
    abandoned (the underlying OS thread finishes on its own in the
    background and its result is simply discarded) and the loop
    continues on its normal cadence.
  - check() reports whether the task looks frozen (no completed cycle
    within a generous multiple of the currently effective poll
    interval), which health_service.get_health() now surfaces.

Does not touch main.py, scanner engines, or trading logic -- it only
watches this one dashboard-process background task.
"""

import threading
import time


class CycleWatchdog:
    """
    Thread-safe: poll_loop's single background task is the only
    writer (cycle_started/cycle_completed/cycle_timed_out), but
    check() is read concurrently from request-handling threads via
    health_service.get_health().
    """

    def __init__(self, stall_multiplier: int = 4):
        self._lock = threading.Lock()
        self._last_cycle_started_at = None
        self._last_cycle_completed_at = None
        self._last_cycle_duration_seconds = None
        self._timeout_count = 0
        # "Frozen" is judged relative to how often a cycle *should*
        # complete right now -- a stall_multiplier of 4 means: only
        # flag frozen once we've gone 4x longer than the current poll
        # interval without a single completed cycle, so ordinary
        # backoff (see live_service.poll_loop) is never mistaken for
        # a freeze.
        self._stall_multiplier = stall_multiplier

    def cycle_started(self) -> None:
        with self._lock:
            self._last_cycle_started_at = time.monotonic()

    def cycle_completed(self) -> None:
        with self._lock:
            now = time.monotonic()
            self._last_cycle_completed_at = now
            if self._last_cycle_started_at is not None:
                self._last_cycle_duration_seconds = round(now - self._last_cycle_started_at, 3)

    def cycle_timed_out(self) -> None:
        with self._lock:
            self._timeout_count += 1

    def check(self, expected_interval_seconds: float) -> dict:
        with self._lock:
            never_completed = self._last_cycle_completed_at is None
            seconds_since_last_cycle = (
                None if never_completed
                else round(time.monotonic() - self._last_cycle_completed_at, 3)
            )
            stall_threshold = expected_interval_seconds * self._stall_multiplier
            frozen = (
                not never_completed
                and seconds_since_last_cycle is not None
                and seconds_since_last_cycle > stall_threshold
            )
            return {
                "frozen": frozen,
                "never_completed_a_cycle": never_completed,
                "seconds_since_last_cycle": seconds_since_last_cycle,
                "last_cycle_duration_seconds": self._last_cycle_duration_seconds,
                "timeout_count": self._timeout_count,
                "stall_threshold_seconds": stall_threshold,
            }
