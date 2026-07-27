"""
scripts/production/tests/test_part10_phase5.py -- unit tests for
Part 10 Phase 5: runtime_diagnostics (startup report, runtime report,
uptime/peak/availability trackers).
------------------------------------------------------------------------------
Plain assert-based, no pytest dependency. Fully offline / in-process.

Run with: python scripts/production/tests/test_part10_phase5.py
"""

import os
import sys
import time

_here = os.path.dirname(os.path.abspath(__file__))
_scripts_dir = os.path.dirname(os.path.dirname(_here))
sys.path.insert(0, _scripts_dir)

from production.runtime_diagnostics import (
    ProcessUptimeTracker, PeakTracker, AvailabilityTracker,
    generate_startup_report, generate_runtime_report,
)
from production.health_states import HealthState
from production.startup_validator import StartupReport, CheckStatus
from production.broker_monitor import BrokerMonitor
from production.telegram_monitor import TelegramMonitor
from production.scanner_monitor import ScannerMonitor
from production.api_monitor import APIMonitor
from production.websocket_monitor import WebSocketMonitor
from production.dashboard_monitor import DashboardMonitor
from production.resource_monitor import ResourceMonitor
from production.alert_engine import AlertEngine, AlertSources
from production.task_manager import TaskManager
from production.health_registry import HealthRegistry


# -- ProcessUptimeTracker ---------------------------------------------

def test_uptime_tracker_increases_over_time():
    t = ProcessUptimeTracker()
    first = t.uptime_seconds()
    time.sleep(0.01)
    second = t.uptime_seconds()
    assert second > first


# -- PeakTracker ---------------------------------------------

def test_peak_tracker_keeps_highest_value():
    p = PeakTracker()
    p.sample("cpu_pct", 10.0)
    p.sample("cpu_pct", 55.0)
    p.sample("cpu_pct", 30.0)
    assert p.peak("cpu_pct") == 55.0


def test_peak_tracker_ignores_none():
    p = PeakTracker()
    p.sample("memory_pct", None)
    assert p.peak("memory_pct") is None


def test_peak_tracker_unknown_metric_returns_none():
    p = PeakTracker()
    assert p.peak("never_sampled") is None


def test_peak_tracker_reset_clears_all():
    p = PeakTracker()
    p.sample("cpu_pct", 90.0)
    p.reset()
    assert p.peak("cpu_pct") is None


# -- AvailabilityTracker ---------------------------------------------

def test_availability_tracker_percentage():
    a = AvailabilityTracker()
    a.record(HealthState.HEALTHY)
    a.record(HealthState.HEALTHY)
    a.record(HealthState.HEALTHY)
    a.record(HealthState.CRITICAL)
    assert a.availability_pct() == 75.0


def test_availability_tracker_no_samples_returns_none():
    a = AvailabilityTracker()
    assert a.availability_pct() is None


def test_availability_tracker_bounded_history():
    a = AvailabilityTracker(max_samples=3)
    for _ in range(5):
        a.record(HealthState.HEALTHY)
    assert a.sample_count() == 3


# -- Startup report ---------------------------------------------

def test_startup_report_with_no_inputs_is_ok():
    report = generate_startup_report()
    assert report.ok is True
    assert report.components == []


def test_startup_report_includes_component_states():
    broker = BrokerMonitor()  # OFFLINE (never connected)
    telegram = TelegramMonitor()  # HEALTHY by default
    report = generate_startup_report(broker_status=broker.health, telegram_status=telegram.health)
    names = {c.name for c in report.components}
    assert names == {"broker", "telegram"}
    assert report.ok is False  # broker OFFLINE


def test_startup_report_reflects_critical_validation_failures():
    sv = StartupReport()
    sv.add("python_version", CheckStatus.CRITICAL, "unsupported")
    report = generate_startup_report(startup_validation=sv)
    assert report.ok is False


def test_startup_report_broken_probe_recorded_as_offline_not_raised():
    def _raises():
        raise RuntimeError("boom")
    report = generate_startup_report(broker_status=_raises)
    assert report.components[0].state == "OFFLINE"
    assert "boom" in report.components[0].message


def test_startup_report_render_does_not_raise():
    report = generate_startup_report(telegram_status=TelegramMonitor().health)
    text = report.render()
    assert "STARTUP DIAGNOSTICS" in text


# -- Runtime report ---------------------------------------------

def test_runtime_report_with_no_inputs_has_only_uptime():
    report = generate_runtime_report(
        uptime_tracker=ProcessUptimeTracker(), peak_tracker=PeakTracker(),
        availability_tracker=AvailabilityTracker(),
    )
    assert report.uptime_seconds >= 0
    assert report.average_scan_time_seconds is None
    assert report.worker_status == []
    assert report.health_score is None


def test_runtime_report_scanner_and_api_durations():
    scanner = ScannerMonitor()
    scanner.scan_started()
    time.sleep(0.01)
    scanner.scan_completed(alert_count=0)
    api = APIMonitor()
    api.request_started("/api/health")
    api.record_request("/api/health", duration_ms=42.0, success=True)

    report = generate_runtime_report(
        scanner_monitor=scanner, api_monitor=api,
        uptime_tracker=ProcessUptimeTracker(), peak_tracker=PeakTracker(),
        availability_tracker=AvailabilityTracker(),
    )
    assert report.average_scan_time_seconds is not None and report.average_scan_time_seconds > 0
    assert report.average_api_time_ms == 42.0


def test_runtime_report_worker_status_and_restarts():
    tm = TaskManager()
    tm.register("scanner_thread", target=lambda stop_event, heartbeat: None, heartbeat_timeout=30.0)

    report = generate_runtime_report(
        task_manager=tm, uptime_tracker=ProcessUptimeTracker(),
        peak_tracker=PeakTracker(), availability_tracker=AvailabilityTracker(),
    )
    assert len(report.worker_status) == 1
    assert report.worker_status[0]["name"] == "scanner_thread"


def test_runtime_report_connection_status():
    broker = BrokerMonitor()
    broker.record_connect()
    ws = WebSocketMonitor()
    ws.record_connect(client_key=1)

    report = generate_runtime_report(
        broker_monitor=broker, websocket_monitor=ws,
        uptime_tracker=ProcessUptimeTracker(), peak_tracker=PeakTracker(),
        availability_tracker=AvailabilityTracker(),
    )
    assert report.connection_status["broker_connected"] is True
    assert report.connection_status["websocket_active_clients"] == 1


def test_runtime_report_health_score_and_availability():
    registry = HealthRegistry()
    registry.register("telegram", TelegramMonitor().health)  # healthy
    availability = AvailabilityTracker()

    report = generate_runtime_report(
        health_registry=registry, uptime_tracker=ProcessUptimeTracker(),
        peak_tracker=PeakTracker(), availability_tracker=availability,
    )
    # health_registry.check_all() reports on every KNOWN_COMPONENTS
    # entry, not just the ones registered here -- unregistered ones
    # are "not monitored" OFFLINE, so the score reflects that mix
    # rather than being 100 just because the one component we did
    # register is healthy.
    assert report.health_score is not None
    assert 0.0 <= report.health_score <= 100.0
    assert report.availability_pct is not None


def test_runtime_report_alerts_today_from_alert_engine():
    engine = AlertEngine()
    sources = AlertSources(broker_status=BrokerMonitor().health)  # OFFLINE -> CRITICAL alert
    report = generate_runtime_report(
        alert_engine=engine, alert_sources=sources,
        uptime_tracker=ProcessUptimeTracker(), peak_tracker=PeakTracker(),
        availability_tracker=AvailabilityTracker(),
    )
    assert report.alerts_today == 1
    assert report.errors_today == 1


def test_runtime_report_peak_tracking_accumulates_across_calls():
    rm = ResourceMonitor()
    peaks = PeakTracker()
    generate_runtime_report(resource_monitor=rm, uptime_tracker=ProcessUptimeTracker(),
                             peak_tracker=peaks, availability_tracker=AvailabilityTracker())
    first_peak = peaks.peak("memory_pct")
    # A second call with a manually-injected higher reading must not
    # lower the tracked peak even if the "current" reading afterwards
    # is lower -- peaks only go up.
    peaks.sample("memory_pct", 999.0)
    generate_runtime_report(resource_monitor=rm, uptime_tracker=ProcessUptimeTracker(),
                             peak_tracker=peaks, availability_tracker=AvailabilityTracker())
    assert peaks.peak("memory_pct") == 999.0


def test_runtime_report_to_dict_shape():
    report = generate_runtime_report(
        uptime_tracker=ProcessUptimeTracker(), peak_tracker=PeakTracker(),
        availability_tracker=AvailabilityTracker(),
    )
    d = report.to_dict()
    assert "uptime_seconds" in d and "production_score" in d


ALL_TESTS = [
    test_uptime_tracker_increases_over_time,
    test_peak_tracker_keeps_highest_value,
    test_peak_tracker_ignores_none,
    test_peak_tracker_unknown_metric_returns_none,
    test_peak_tracker_reset_clears_all,
    test_availability_tracker_percentage,
    test_availability_tracker_no_samples_returns_none,
    test_availability_tracker_bounded_history,
    test_startup_report_with_no_inputs_is_ok,
    test_startup_report_includes_component_states,
    test_startup_report_reflects_critical_validation_failures,
    test_startup_report_broken_probe_recorded_as_offline_not_raised,
    test_startup_report_render_does_not_raise,
    test_runtime_report_with_no_inputs_has_only_uptime,
    test_runtime_report_scanner_and_api_durations,
    test_runtime_report_worker_status_and_restarts,
    test_runtime_report_connection_status,
    test_runtime_report_health_score_and_availability,
    test_runtime_report_alerts_today_from_alert_engine,
    test_runtime_report_peak_tracking_accumulates_across_calls,
    test_runtime_report_to_dict_shape,
]


if __name__ == "__main__":
    passed = 0
    for test_fn in ALL_TESTS:
        test_fn()
        passed += 1
        print(f"PASS: {test_fn.__name__}")
    print(f"\n{passed}/{len(ALL_TESTS)} passed")
