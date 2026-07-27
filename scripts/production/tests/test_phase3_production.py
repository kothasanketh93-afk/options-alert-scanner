"""
scripts/production/tests/test_phase3_production.py -- unit tests for
Part 9 Phase 3: resource_monitor and the pure-Python half of
api_protection (pagination/envelope helpers -- the Starlette
middleware classes need fastapi/starlette, which isn't installed in
this offline sandbox; see MERGE_SUMMARY for the same limitation
affecting app/backend's own tests).
------------------------------------------------------------------------------
Plain assert-based, no pytest dependency (see test_phase1_production.py
for the same convention).

Run with: python scripts/production/tests/test_phase3_production.py
"""

import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
_scripts_dir = os.path.dirname(os.path.dirname(_here))
sys.path.insert(0, _scripts_dir)

from production.resource_monitor import (
    ResourceMonitor, ResourceThresholds, ResourceSnapshot, AlertLevel,
)
from production.api_protection import (
    paginate, validate_pagination_params, PaginationError, error_envelope,
)
from exceptions import ResourceLimitExceeded


# --------------------------------------------------------------------
# resource_monitor
# --------------------------------------------------------------------

def test_thresholds_reject_warning_above_critical():
    try:
        ResourceThresholds(memory_warning_pct=95.0, memory_critical_pct=90.0)
        assert False, "expected ValueError: warning above critical"
    except ValueError:
        pass


def test_evaluate_flags_warning_and_critical_correctly():
    thresholds = ResourceThresholds(
        memory_warning_pct=70.0, memory_critical_pct=90.0,
        cpu_warning_pct=70.0, cpu_critical_pct=90.0,
        disk_warning_pct=70.0, disk_critical_pct=90.0,
        thread_warning_count=10, thread_critical_count=20,
    )
    monitor = ResourceMonitor(thresholds=thresholds)
    snapshot = ResourceSnapshot(
        timestamp=0.0, memory_pct=95.0, cpu_pct=75.0, disk_pct=50.0, thread_count=15,
    )
    alerts = monitor.evaluate(snapshot)
    by_resource = {a.resource: a for a in alerts}
    assert by_resource["memory_pct"].level is AlertLevel.CRITICAL
    assert by_resource["cpu_pct"].level is AlertLevel.WARNING
    assert "disk_pct" not in by_resource
    assert by_resource["thread_count"].level is AlertLevel.WARNING


def test_evaluate_ignores_none_values_without_crashing():
    monitor = ResourceMonitor()
    snapshot = ResourceSnapshot(timestamp=0.0, memory_pct=None, cpu_pct=None, disk_pct=None, thread_count=3)
    alerts = monitor.evaluate(snapshot)
    assert all(a.resource != "memory_pct" for a in alerts)
    assert all(a.resource != "cpu_pct" for a in alerts)


def test_custom_gauge_is_sampled_and_evaluated():
    monitor = ResourceMonitor()
    monitor.register_gauge("queue_size", lambda: 5000)  # way over the 2000 critical default
    snapshot = monitor.sample()
    assert snapshot.gauges["queue_size"] == 5000.0
    alerts = monitor.evaluate(snapshot)
    queue_alerts = [a for a in alerts if a.resource == "queue_size"]
    assert queue_alerts and queue_alerts[0].level is AlertLevel.CRITICAL


def test_broken_gauge_does_not_crash_sampling():
    monitor = ResourceMonitor()

    def _boom():
        raise RuntimeError("gauge exploded")

    monitor.register_gauge("broken", _boom)
    snapshot = monitor.sample()  # must not raise
    assert "broken" not in snapshot.gauges


def test_raise_if_critical_raises_resource_limit_exceeded():
    monitor = ResourceMonitor()
    snapshot = ResourceSnapshot(timestamp=0.0, memory_pct=99.0, cpu_pct=None, disk_pct=None, thread_count=1)
    alerts = monitor.evaluate(snapshot)
    try:
        monitor.raise_if_critical(alerts)
        assert False, "expected ResourceLimitExceeded"
    except ResourceLimitExceeded as exc:
        assert exc.resource == "memory_pct"


def test_raise_if_critical_is_a_noop_when_no_critical_alerts():
    monitor = ResourceMonitor()
    snapshot = ResourceSnapshot(timestamp=0.0, memory_pct=10.0, cpu_pct=10.0, disk_pct=10.0, thread_count=1)
    alerts = monitor.evaluate(snapshot)
    monitor.raise_if_critical(alerts)  # must not raise


def test_sample_and_evaluate_round_trip_uses_real_disk():
    monitor = ResourceMonitor(disk_path=".")
    snapshot, alerts = monitor.sample_and_evaluate()
    assert snapshot.disk_pct is None or 0.0 <= snapshot.disk_pct <= 100.0
    assert isinstance(alerts, list)


# --------------------------------------------------------------------
# api_protection (pure-Python half)
# --------------------------------------------------------------------

def test_paginate_basic_slicing():
    items = list(range(25))
    result = paginate(items, page=2, page_size=10)
    assert result["items"] == list(range(10, 20))
    assert result["total"] == 25
    assert result["total_pages"] == 3
    assert result["page"] == 2
    assert result["page_size"] == 10


def test_paginate_last_partial_page():
    items = list(range(25))
    result = paginate(items, page=3, page_size=10)
    assert result["items"] == [20, 21, 22, 23, 24]


def test_paginate_page_beyond_range_returns_empty_items():
    items = list(range(5))
    result = paginate(items, page=5, page_size=10)
    assert result["items"] == []
    assert result["total"] == 5


def test_validate_pagination_params_rejects_bad_input():
    for page, page_size in [(0, 10), (-1, 10), (1, 0), (1, -5), (1, 10_000)]:
        try:
            validate_pagination_params(page, page_size)
            assert False, f"expected PaginationError for page={page}, page_size={page_size}"
        except PaginationError:
            pass


def test_validate_pagination_params_accepts_good_input():
    params = validate_pagination_params(1, 50)
    assert params.page == 1
    assert params.page_size == 50


def test_error_envelope_shape():
    env = error_envelope(404, "NotFound", "no such resource", request_id="abc-123")
    assert env["status_code"] == 404
    assert env["error"]["type"] == "NotFound"
    assert env["error"]["message"] == "no such resource"
    assert env["error"]["request_id"] == "abc-123"


ALL_TESTS = [
    test_thresholds_reject_warning_above_critical,
    test_evaluate_flags_warning_and_critical_correctly,
    test_evaluate_ignores_none_values_without_crashing,
    test_custom_gauge_is_sampled_and_evaluated,
    test_broken_gauge_does_not_crash_sampling,
    test_raise_if_critical_raises_resource_limit_exceeded,
    test_raise_if_critical_is_a_noop_when_no_critical_alerts,
    test_sample_and_evaluate_round_trip_uses_real_disk,
    test_paginate_basic_slicing,
    test_paginate_last_partial_page,
    test_paginate_page_beyond_range_returns_empty_items,
    test_validate_pagination_params_rejects_bad_input,
    test_validate_pagination_params_accepts_good_input,
    test_error_envelope_shape,
]


if __name__ == "__main__":
    passed = 0
    for test_fn in ALL_TESTS:
        test_fn()
        passed += 1
        print(f"PASS: {test_fn.__name__}")
    print(f"\n{passed}/{len(ALL_TESTS)} passed")
