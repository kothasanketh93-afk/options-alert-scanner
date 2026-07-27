"""
scripts/production/tests/test_phase6_production.py -- unit tests for
Part 9 Phase 6: performance.py and production_report.py.
------------------------------------------------------------------------------
Plain assert-based, no pytest dependency (see test_phase1_production.py
for the same convention).

Run with: python scripts/production/tests/test_phase6_production.py
"""

import os
import sys
import time

_here = os.path.dirname(os.path.abspath(__file__))
_scripts_dir = os.path.dirname(os.path.dirname(_here))
sys.path.insert(0, _scripts_dir)

from production.performance import memoize, timed, Timer
from production.cache_manager import ManagedCache
from production.config_validator import ConfigValidationReport, Severity
from production.startup_validator import StartupReport, CheckStatus
from production.production_report import generate_production_report, IssueSeverity


# --------------------------------------------------------------------
# performance.py
# --------------------------------------------------------------------

def test_memoize_dedupes_repeated_calls():
    calls = {"n": 0}
    cache = ManagedCache()

    @memoize(ttl_seconds=10, cache=cache)
    def expensive(x, y=1):
        calls["n"] += 1
        return x + y

    assert expensive(2, y=3) == 5
    assert expensive(2, y=3) == 5
    assert calls["n"] == 1


def test_memoize_distinguishes_different_arguments():
    calls = {"n": 0}
    cache = ManagedCache()

    @memoize(ttl_seconds=10, cache=cache)
    def f(x):
        calls["n"] += 1
        return x * 2

    assert f(1) == 2
    assert f(2) == 4
    assert calls["n"] == 2


def test_memoize_custom_key_fn():
    calls = {"n": 0}
    cache = ManagedCache()

    @memoize(ttl_seconds=10, cache=cache, key_fn=lambda symbol, **_: f"key:{symbol}")
    def fetch(symbol, verbose=False):
        calls["n"] += 1
        return symbol.upper()

    fetch("nifty", verbose=True)
    fetch("nifty", verbose=False)  # different kwarg, same custom key -> still a hit
    assert calls["n"] == 1


def test_timed_decorator_does_not_change_return_value_or_raise():
    @timed(label="unit_test_fn")
    def f(x):
        return x * 2

    assert f(21) == 42


def test_timed_decorator_propagates_exceptions():
    @timed()
    def boom():
        raise ValueError("nope")

    try:
        boom()
        assert False, "expected ValueError to propagate"
    except ValueError:
        pass


def test_timer_context_manager_records_elapsed():
    with Timer("unit_test_block") as t:
        time.sleep(0.01)
    assert t.elapsed_seconds is not None
    assert t.elapsed_seconds >= 0.01


def test_timer_records_elapsed_even_on_exception():
    t = Timer("unit_test_block_error")
    try:
        with t:
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert t.elapsed_seconds is not None


# --------------------------------------------------------------------
# production_report.py
# --------------------------------------------------------------------

def test_empty_report_has_full_readiness_and_a_recommendation():
    report = generate_production_report()
    assert report.readiness_percent == 100.0
    assert report.sections_assessed == []
    assert len(report.recommendations) == 1


def test_config_errors_become_high_severity_issues():
    cfg_report = ConfigValidationReport()
    cfg_report.add(Severity.ERROR, "MAX_CONCURRENT_TRADES", "must be positive")
    cfg_report.add(Severity.WARNING, "GAP_MIN_PCT", "unusually high")

    report = generate_production_report(config_report=cfg_report)
    assert len(report.by_severity(IssueSeverity.HIGH)) == 1
    assert len(report.by_severity(IssueSeverity.MEDIUM)) == 1
    assert "config_validation" in report.sections_assessed


def test_startup_critical_failure_becomes_critical_issue():
    startup = StartupReport()
    startup.add("dependencies", CheckStatus.CRITICAL, "missing fastapi")
    startup.add("python_version", CheckStatus.OK, "3.12")

    report = generate_production_report(startup_report=startup)
    critical = report.by_severity(IssueSeverity.CRITICAL)
    assert len(critical) == 1
    assert "dependencies" in critical[0].message or critical[0].source == "startup"


def test_readiness_percent_deducts_by_severity():
    cfg_report = ConfigValidationReport()
    cfg_report.add(Severity.ERROR, "A", "bad")  # HIGH -> -10

    startup = StartupReport()
    startup.add("x", CheckStatus.CRITICAL, "bad")  # CRITICAL -> -25

    report = generate_production_report(config_report=cfg_report, startup_report=startup)
    assert report.readiness_percent == 100 - 25 - 10


def test_extended_health_resource_alerts_and_crashed_worker_become_issues():
    fake_health = {
        "base": {"issues": ["state branch unreachable"]},
        "resources": {
            "status": "ok",
            "memory_pct": 95.0, "cpu_pct": 40.0, "disk_pct": 50.0, "thread_count": 12,
            "alerts": [{"resource": "memory_pct", "level": "critical", "value": 95.0, "message": "memory critical"}],
        },
        "background_tasks": {
            "status": "ok", "running_count": 2, "unhealthy_count": 1,
            "workers": [{"name": "w1", "state": "crashed", "last_error": "boom"}],
        },
        "broker": {"status": "error", "error": "timeout"},
        "telegram": {"status": "ok"},
        "api": {"status": "not_configured"},
        "websocket": {"status": "not_configured"},
    }
    report = generate_production_report(extended_health=fake_health)
    sources = [i.source for i in report.issues]
    assert "resources" in sources
    assert "background_tasks" in sources
    assert "broker" in sources
    assert "base_health" in sources
    assert report.memory_summary["memory_pct"] == 95.0
    assert report.thread_summary["running_workers"] == 2


def test_cache_stats_low_hit_ratio_produces_recommendation():
    stats = {"entries": 10, "hits": 2, "misses": 18, "evictions": 0}
    report = generate_production_report(cache_stats=stats)
    assert report.api_summary["cache_hit_ratio"] == 0.1
    assert any("hit ratio is low" in r for r in report.recommendations)


def test_cache_stats_healthy_hit_ratio_produces_no_recommendation():
    stats = {"entries": 10, "hits": 90, "misses": 10, "evictions": 0}
    report = generate_production_report(cache_stats=stats)
    assert not any("hit ratio is low" in r for r in report.recommendations)


def test_render_contains_all_severity_buckets_and_score():
    report = generate_production_report()
    text = report.render()
    assert "CRITICAL (0)" in text
    assert "Readiness: 100%" in text


ALL_TESTS = [
    test_memoize_dedupes_repeated_calls,
    test_memoize_distinguishes_different_arguments,
    test_memoize_custom_key_fn,
    test_timed_decorator_does_not_change_return_value_or_raise,
    test_timed_decorator_propagates_exceptions,
    test_timer_context_manager_records_elapsed,
    test_timer_records_elapsed_even_on_exception,
    test_empty_report_has_full_readiness_and_a_recommendation,
    test_config_errors_become_high_severity_issues,
    test_startup_critical_failure_becomes_critical_issue,
    test_readiness_percent_deducts_by_severity,
    test_extended_health_resource_alerts_and_crashed_worker_become_issues,
    test_cache_stats_low_hit_ratio_produces_recommendation,
    test_cache_stats_healthy_hit_ratio_produces_no_recommendation,
    test_render_contains_all_severity_buckets_and_score,
]


if __name__ == "__main__":
    passed = 0
    for test_fn in ALL_TESTS:
        test_fn()
        passed += 1
        print(f"PASS: {test_fn.__name__}")
    print(f"\n{passed}/{len(ALL_TESTS)} passed")
