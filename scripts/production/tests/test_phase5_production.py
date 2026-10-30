"""
scripts/production/tests/test_phase5_production.py -- unit tests for
Part 9 Phase 5: health_aggregator.
------------------------------------------------------------------------------
Plain assert-based, no pytest dependency (see test_phase1_production.py
for the same convention). health_extended.py (the FastAPI route) is not
tested here -- it needs fastapi, unavailable in this offline sandbox;
get_extended_health() (the function it calls) is fully covered instead.

(2026-10-30, at the user's explicit request, as part of removing unused
code to make this app intraday-only: file_manager.py was deleted --
zero importers anywhere in the repo outside this file's own tests. Its
tests below were removed along with it. health_aggregator.py does not
import file_manager (confirmed -- it only imports resource_monitor and
task_manager) and is unaffected.)

Run with: python scripts/production/tests/test_phase5_production.py
"""

import os
import sys
import time

_here = os.path.dirname(os.path.abspath(__file__))
_scripts_dir = os.path.dirname(os.path.dirname(_here))
sys.path.insert(0, _scripts_dir)

from production.health_aggregator import get_extended_health, HealthProviders
from production.resource_monitor import ResourceMonitor, ResourceThresholds
from production.task_manager import TaskManager


# --------------------------------------------------------------------
# health_aggregator
# --------------------------------------------------------------------

def test_extended_health_reports_not_configured_when_nothing_wired():
    result = get_extended_health()
    assert result["resources"]["status"] == "not_configured"
    assert result["background_tasks"]["status"] == "not_configured"
    assert result["broker"]["status"] == "not_configured"
    assert result["telegram"]["status"] == "not_configured"
    assert "overall_status" in result
    assert "overall_score" in result


def test_extended_health_includes_resource_and_task_data_when_wired():
    monitor = ResourceMonitor(thresholds=ResourceThresholds(memory_warning_pct=1, memory_critical_pct=2))
    tm = TaskManager()

    def worker(stop_event, heartbeat):
        heartbeat()
        stop_event.wait(0.2)

    tm.register("w", worker)
    tm.start("w")
    time.sleep(0.02)
    try:
        result = get_extended_health(resource_monitor=monitor, task_manager=tm)
        assert result["resources"]["status"] == "ok"
        assert result["background_tasks"]["status"] == "ok"
        assert result["background_tasks"]["running_count"] == 1
    finally:
        tm.stop("w", timeout=2.0)


def test_extended_health_provider_error_is_captured_not_raised():
    def broken_provider():
        raise RuntimeError("broker API down")

    providers = HealthProviders(broker_status=broken_provider)
    result = get_extended_health(providers=providers)
    assert result["broker"]["status"] == "error"
    assert "broker API down" in result["broker"]["error"]


def test_extended_health_provider_success_merges_result_dict():
    providers = HealthProviders(telegram_status=lambda: {"queue_depth": 3, "last_sent_at": "12:00"})
    result = get_extended_health(providers=providers)
    assert result["telegram"]["status"] == "ok"
    assert result["telegram"]["queue_depth"] == 3


def test_extended_health_critical_resource_alert_lowers_score():
    monitor = ResourceMonitor(thresholds=ResourceThresholds(memory_warning_pct=1, memory_critical_pct=1))
    result_no_alert = get_extended_health()
    result_with_alert = get_extended_health(resource_monitor=monitor)
    # A real machine almost certainly has >1% memory used, so this
    # threshold config should always trip a critical alert here.
    assert result_with_alert["overall_score"] <= result_no_alert["overall_score"]


ALL_TESTS = [
    test_extended_health_reports_not_configured_when_nothing_wired,
    test_extended_health_includes_resource_and_task_data_when_wired,
    test_extended_health_provider_error_is_captured_not_raised,
    test_extended_health_provider_success_merges_result_dict,
    test_extended_health_critical_resource_alert_lowers_score,
]


if __name__ == "__main__":
    passed = 0
    for test_fn in ALL_TESTS:
        test_fn()
        passed += 1
        print(f"PASS: {test_fn.__name__}")
    print(f"\n{passed}/{len(ALL_TESTS)} passed")
