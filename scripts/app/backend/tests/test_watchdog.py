"""
Unit tests for Part 10's watchdog.py. Deliberately has NO dependency
on state.py/config.py/daily_cache.py (unlike the other test files in
this directory) -- watchdog.py itself only uses threading/time, so
these tests can run in isolation without the rest of the scanner repo
present, and are a fast way to verify the watchdog logic on its own.
"""

import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "scripts"))

from app.backend.services.watchdog import CycleWatchdog


def test_never_completed_is_not_frozen():
    """No cycle has run yet (e.g. right after process startup) --
    that's an honest 'unknown', not a freeze."""
    wd = CycleWatchdog()
    status = wd.check(expected_interval_seconds=15)
    assert status["never_completed_a_cycle"] is True
    assert status["frozen"] is False


def test_recent_completed_cycle_is_not_frozen():
    wd = CycleWatchdog()
    wd.cycle_started()
    wd.cycle_completed()
    status = wd.check(expected_interval_seconds=15)
    assert status["never_completed_a_cycle"] is False
    assert status["frozen"] is False
    assert status["last_cycle_duration_seconds"] is not None


def test_stale_completed_cycle_is_frozen():
    """Simulate a cycle that completed long ago relative to the
    expected interval (stall_multiplier defaults to 4x)."""
    wd = CycleWatchdog(stall_multiplier=4)
    wd.cycle_started()
    wd.cycle_completed()
    # Force the recorded completion time far enough in the past that
    # it exceeds 4x a 1-second expected interval, without an actual
    # multi-second sleep in the test suite.
    wd._last_cycle_completed_at -= 10  # noqa: SLF001 -- intentional, this is the module's own test
    status = wd.check(expected_interval_seconds=1)
    assert status["frozen"] is True


def test_timeout_count_increments():
    wd = CycleWatchdog()
    assert wd.check(expected_interval_seconds=15)["timeout_count"] == 0
    wd.cycle_timed_out()
    wd.cycle_timed_out()
    assert wd.check(expected_interval_seconds=15)["timeout_count"] == 2


def test_duration_reflects_actual_elapsed_time():
    wd = CycleWatchdog()
    wd.cycle_started()
    time.sleep(0.05)
    wd.cycle_completed()
    status = wd.check(expected_interval_seconds=15)
    assert status["last_cycle_duration_seconds"] >= 0.05
