"""
scripts/production/tests/test_phase1_production.py -- unit tests for
Part 9 Phase 1: shutdown_manager, and the exceptions.py additions.
------------------------------------------------------------------------------
Plain assert-based, no pytest dependency (this repo doesn't use one --
see requirements.txt / test_telegram.py's own manual-script convention).
Everything here runs fully offline against tmp directories / in-process
state -- no broker, network, or filesystem outside a scratch tmp dir.

(2026-10-30, at the user's explicit request, as part of removing unused
code to make this app intraday-only: logging_manager.py and
retry_manager.py were deleted -- zero importers anywhere in the repo
outside this file's own tests (logging_setup.py and retry_utils.py,
the pre-existing modules they were meant to optionally be migrated
onto, were never actually migrated). Their tests below were removed
along with them. shutdown_manager.py and the exceptions.py additions
are unaffected and kept.)

Run with: python scripts/production/tests/test_phase1_production.py
Exits non-zero (via AssertionError) on the first failing check.
"""

import os
import sys

# scripts/ needs to be on sys.path -- this file sits 2 levels below
# scripts/ (scripts/production/tests/).
_here = os.path.dirname(os.path.abspath(__file__))
_scripts_dir = os.path.dirname(os.path.dirname(_here))
sys.path.insert(0, _scripts_dir)

from production.shutdown_manager import ShutdownManager
from exceptions import (
    CircuitBreakerOpenError, RetryExhaustedError, StartupValidationError,
    ResourceLimitExceeded, WorkerError, CacheError, TradingSystemError,
)


# --------------------------------------------------------------------
# shutdown_manager
# --------------------------------------------------------------------

def test_shutdown_manager_runs_hooks_in_reverse_order():
    order = []
    mgr = ShutdownManager()
    mgr.register("first", lambda: order.append("first"))
    mgr.register("second", lambda: order.append("second"))
    mgr.register("third", lambda: order.append("third"))

    report = mgr.shutdown(reason="unit_test")
    assert order == ["third", "second", "first"]
    assert report.completed == ["third", "second", "first"]
    assert report.failed == []


def test_shutdown_manager_continues_past_a_failing_hook():
    order = []
    mgr = ShutdownManager()
    mgr.register("ok_1", lambda: order.append("ok_1"))

    def _boom():
        raise RuntimeError("hook failed")

    mgr.register("boom", _boom)
    mgr.register("ok_2", lambda: order.append("ok_2"))

    report = mgr.shutdown(reason="unit_test")
    assert "ok_1" in report.completed
    assert "ok_2" in report.completed
    assert report.failed == ["boom"]
    assert order == ["ok_2", "ok_1"]


def test_shutdown_manager_is_idempotent():
    calls = {"n": 0}
    mgr = ShutdownManager()
    mgr.register("count", lambda: calls.__setitem__("n", calls["n"] + 1))

    report1 = mgr.shutdown(reason="first_call")
    report2 = mgr.shutdown(reason="second_call")

    assert calls["n"] == 1, "hooks must not run twice on repeated shutdown() calls"
    assert report1 is report2


# --------------------------------------------------------------------
# exceptions.py additions
# --------------------------------------------------------------------

def test_new_exceptions_are_trading_system_errors():
    for cls in (CircuitBreakerOpenError, RetryExhaustedError, StartupValidationError,
                ResourceLimitExceeded, WorkerError, CacheError):
        assert issubclass(cls, TradingSystemError)


def test_circuit_breaker_open_error_carries_context():
    exc = CircuitBreakerOpenError("blocked", breaker_name="angelone", retry_after=12.5)
    assert exc.breaker_name == "angelone"
    assert exc.retry_after == 12.5


def test_resource_limit_exceeded_carries_context():
    exc = ResourceLimitExceeded("memory too high", resource="memory", value=95.0, threshold=90.0)
    assert exc.resource == "memory"
    assert exc.value == 95.0
    assert exc.threshold == 90.0


ALL_TESTS = [
    test_shutdown_manager_runs_hooks_in_reverse_order,
    test_shutdown_manager_continues_past_a_failing_hook,
    test_shutdown_manager_is_idempotent,
    test_new_exceptions_are_trading_system_errors,
    test_circuit_breaker_open_error_carries_context,
    test_resource_limit_exceeded_carries_context,
]


if __name__ == "__main__":
    passed = 0
    for test_fn in ALL_TESTS:
        test_fn()
        passed += 1
        print(f"PASS: {test_fn.__name__}")
    print(f"\n{passed}/{len(ALL_TESTS)} passed")
