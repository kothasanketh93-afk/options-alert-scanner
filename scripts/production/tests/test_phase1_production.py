"""
scripts/production/tests/test_phase1_production.py -- unit tests for
Part 9 Phase 1: logging_manager, retry_manager, shutdown_manager, and
the new exceptions.py additions.
------------------------------------------------------------------------------
Plain assert-based, no pytest dependency (this repo doesn't use one --
see requirements.txt / test_telegram.py's own manual-script convention).
Everything here runs fully offline against tmp directories / in-process
state -- no broker, network, or filesystem outside a scratch tmp dir.

Run with: python scripts/production/tests/test_phase1_production.py
Exits non-zero (via AssertionError) on the first failing check.
"""

import gzip
import os
import shutil
import sys
import tempfile
import time

# scripts/ needs to be on sys.path -- this file sits 2 levels below
# scripts/ (scripts/production/tests/).
_here = os.path.dirname(os.path.abspath(__file__))
_scripts_dir = os.path.dirname(os.path.dirname(_here))
sys.path.insert(0, _scripts_dir)

from production.logging_manager import LoggingManager, LoggingConfig
from production.retry_manager import (
    RetryManager, RetryPolicy, CircuitBreakerConfig, CircuitState, retry,
)
from production.shutdown_manager import ShutdownManager
from exceptions import (
    CircuitBreakerOpenError, RetryExhaustedError, StartupValidationError,
    ResourceLimitExceeded, WorkerError, CacheError, TradingSystemError,
)


def _tmp_dir():
    d = tempfile.mkdtemp(prefix="part9_test_")
    return d


# --------------------------------------------------------------------
# logging_manager
# --------------------------------------------------------------------

def test_logging_manager_creates_one_file_per_channel():
    tmp = _tmp_dir()
    try:
        mgr = LoggingManager(LoggingConfig(log_dir=tmp, also_log_to_stdout=False))
        for channel in ("scanner", "broker", "websocket"):
            logger = mgr.get_logger(channel)
            logger.info("hello from %s", channel)
        for h in mgr._loggers.values():
            for handler in h.handlers:
                handler.flush()
        for channel in ("scanner", "broker", "websocket"):
            path = os.path.join(tmp, f"{channel}.log")
            assert os.path.exists(path), f"expected log file for {channel}"
            with open(path) as f:
                content = f.read()
            assert "hello from " + channel in content
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_logging_manager_get_logger_is_idempotent_no_duplicate_handlers():
    tmp = _tmp_dir()
    try:
        mgr = LoggingManager(LoggingConfig(log_dir=tmp, also_log_to_stdout=False))
        logger1 = mgr.get_logger("scanner")
        logger2 = mgr.get_logger("scanner")
        assert logger1 is logger2
        assert len(logger1.handlers) == 1, "re-fetching a channel must not stack handlers"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_logging_manager_cleanup_deletes_only_expired_archives():
    tmp = _tmp_dir()
    try:
        mgr = LoggingManager(LoggingConfig(log_dir=tmp, retention_days=7, also_log_to_stdout=False))
        old_path = os.path.join(tmp, "scanner.log.1.gz")
        new_path = os.path.join(tmp, "broker.log.1.gz")
        for p in (old_path, new_path):
            with gzip.open(p, "wb") as f:
                f.write(b"x")
        old_time = time.time() - 30 * 86400
        os.utime(old_path, (old_time, old_time))

        deleted = mgr.cleanup_old_logs()
        assert deleted == 1
        assert not os.path.exists(old_path)
        assert os.path.exists(new_path)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------
# retry_manager
# --------------------------------------------------------------------

def test_retry_manager_succeeds_after_transient_failures():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ValueError("transient")
        return "ok"

    mgr = RetryManager()
    result = mgr.call(
        flaky, name="unit_test_flaky",
        policy=RetryPolicy(max_attempts=5, base_backoff=0.001, max_backoff=0.01),
    )
    assert result == "ok"
    assert calls["n"] == 3


def test_retry_manager_raises_retry_exhausted_after_max_attempts():
    def always_fails():
        raise ValueError("nope")

    mgr = RetryManager()
    try:
        mgr.call(
            always_fails, name="unit_test_always_fails",
            policy=RetryPolicy(max_attempts=3, base_backoff=0.001, max_backoff=0.01),
        )
        assert False, "expected RetryExhaustedError"
    except RetryExhaustedError as exc:
        assert exc.attempts == 3
        assert isinstance(exc.last_exception, ValueError)


def test_circuit_breaker_opens_after_threshold_and_blocks_calls():
    def always_fails():
        raise ValueError("nope")

    mgr = RetryManager()
    breaker_cfg = CircuitBreakerConfig(failure_threshold=2, cooldown_seconds=60.0)

    for _ in range(2):
        try:
            mgr.call(
                always_fails, name="unit_test_breaker",
                policy=RetryPolicy(max_attempts=1, base_backoff=0.001),
                breaker_config=breaker_cfg,
            )
        except RetryExhaustedError:
            pass

    breaker = mgr.get_breaker("unit_test_breaker")
    assert breaker.state == CircuitState.OPEN

    try:
        mgr.call(
            always_fails, name="unit_test_breaker",
            policy=RetryPolicy(max_attempts=1, base_backoff=0.001),
            breaker_config=breaker_cfg,
        )
        assert False, "expected CircuitBreakerOpenError while breaker is open"
    except CircuitBreakerOpenError:
        pass


def test_retry_decorator_wraps_function_transparently():
    calls = {"n": 0}

    @retry(name="unit_test_decorator", policy=RetryPolicy(max_attempts=3, base_backoff=0.001),
           use_circuit_breaker=False)
    def sometimes_fails():
        calls["n"] += 1
        if calls["n"] < 2:
            raise RuntimeError("boom")
        return 42

    assert sometimes_fails() == 42
    assert calls["n"] == 2


def test_retry_manager_respects_timeout():
    def slow():
        time.sleep(0.3)
        return "too slow"

    mgr = RetryManager()
    try:
        mgr.call(
            slow, name="unit_test_timeout",
            policy=RetryPolicy(max_attempts=1, timeout=0.05,
                                retryable_exceptions=(TimeoutError,)),
            use_circuit_breaker=False,
        )
        assert False, "expected RetryExhaustedError wrapping a TimeoutError"
    except RetryExhaustedError as exc:
        assert isinstance(exc.last_exception, TimeoutError)


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
    test_logging_manager_creates_one_file_per_channel,
    test_logging_manager_get_logger_is_idempotent_no_duplicate_handlers,
    test_logging_manager_cleanup_deletes_only_expired_archives,
    test_retry_manager_succeeds_after_transient_failures,
    test_retry_manager_raises_retry_exhausted_after_max_attempts,
    test_circuit_breaker_opens_after_threshold_and_blocks_calls,
    test_retry_decorator_wraps_function_transparently,
    test_retry_manager_respects_timeout,
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
