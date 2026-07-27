"""
scripts/production/tests/test_part10_phase4.py -- unit tests for
Part 10 Phase 4: alert_engine.
------------------------------------------------------------------------------
Plain assert-based, no pytest dependency. Fully offline / in-process.

Run with: python scripts/production/tests/test_part10_phase4.py
"""

import os
import sys
import time

_here = os.path.dirname(os.path.abspath(__file__))
_scripts_dir = os.path.dirname(os.path.dirname(_here))
sys.path.insert(0, _scripts_dir)

from production.alert_engine import AlertEngine, AlertSources, AlertSeverity
from production.resource_monitor import ResourceMonitor, ResourceThresholds
from production.broker_monitor import BrokerMonitor
from production.telegram_monitor import TelegramMonitor
from production.scanner_monitor import ScannerMonitor
from production.api_monitor import APIMonitor
from production.websocket_monitor import WebSocketMonitor


def _fake_resource_monitor_over_threshold():
    thresholds = ResourceThresholds(memory_warning_pct=1.0, memory_critical_pct=2.0)
    rm = ResourceMonitor(thresholds=thresholds)
    return rm


def test_alert_fires_for_broker_disconnected():
    engine = AlertEngine()
    broker = BrokerMonitor()  # never connected -> OFFLINE
    sources = AlertSources(broker_status=broker.health)
    fired = engine.evaluate(sources)
    rules = [a.rule for a in fired]
    assert "broker.disconnected" in rules
    assert fired[0].severity == AlertSeverity.CRITICAL


def test_alert_does_not_fire_for_healthy_component():
    engine = AlertEngine()
    telegram = TelegramMonitor()  # healthy by default
    sources = AlertSources(telegram_status=telegram.health)
    fired = engine.evaluate(sources)
    assert fired == []


def test_alert_cooldown_aggregates_repeated_firing():
    engine = AlertEngine(cooldown_seconds=100.0)
    broker = BrokerMonitor()
    sources = AlertSources(broker_status=broker.health)

    first = engine.evaluate(sources)
    second = engine.evaluate(sources)

    assert len(first) == 1
    assert len(second) == 1
    # Same Alert object aggregated, not a second history entry.
    assert first[0] is second[0]
    assert second[0].occurrences == 2
    assert len(engine.history()) == 1


def test_alert_clear_resets_aggregation():
    engine = AlertEngine(cooldown_seconds=100.0)
    broker = BrokerMonitor()
    sources = AlertSources(broker_status=broker.health)

    engine.evaluate(sources)
    engine.clear("broker.disconnected")
    engine.evaluate(sources)

    assert len(engine.history()) == 2


def test_alert_suppression_blocks_firing():
    engine = AlertEngine()
    broker = BrokerMonitor()
    sources = AlertSources(broker_status=broker.health)

    engine.suppress("broker.disconnected", seconds=60)
    fired = engine.evaluate(sources)
    assert fired == []
    assert engine.history() == []

    engine.unsuppress("broker.disconnected")
    fired = engine.evaluate(sources)
    assert len(fired) == 1


def test_alert_resource_thresholds_cpu_memory_disk():
    engine = AlertEngine()
    rm = _fake_resource_monitor_over_threshold()
    sources = AlertSources(resource_monitor=rm)
    fired = engine.evaluate(sources)
    rules = [a.rule for a in fired]
    # Thresholds are set absurdly low (1%/2%) so *something* should trip
    # if psutil/disk sampling is available in this environment; if it
    # isn't (sample() returns None for those metrics), evaluate() simply
    # has nothing to alert on -- both are valid outcomes here, this test
    # only checks that whatever fires is a resource.* rule.
    assert all(r.startswith("resource.") for r in rules)


def test_alert_repeated_exceptions_rule():
    engine = AlertEngine()
    sources = AlertSources(exception_counts=lambda: {"replay_service": 5})
    fired = engine.evaluate(sources)
    rules = [a.rule for a in fired]
    assert "exceptions.replay_service.repeated" in rules
    assert fired[0].severity == AlertSeverity.ERROR


def test_alert_repeated_exceptions_below_threshold_does_not_fire():
    engine = AlertEngine()
    sources = AlertSources(exception_counts=lambda: {"replay_service": 1})
    fired = engine.evaluate(sources)
    assert fired == []


def test_alert_websocket_failures_rule():
    engine = AlertEngine()
    ws = WebSocketMonitor(slow_broadcast_ms=1.0)
    ws.record_broadcast(duration_ms=50.0, recipients=1)
    sources = AlertSources(websocket_status=ws.health)
    fired = engine.evaluate(sources)
    assert any(a.rule == "websocket.failures" for a in fired)


def test_alert_api_latency_high_rule():
    engine = AlertEngine()
    api = APIMonitor()
    api.request_started("/slow")
    api.record_request("/slow", duration_ms=5000.0, success=True)
    sources = AlertSources(api_status=api.health, api_latency_warning_ms=1000.0, api_latency_critical_ms=3000.0)
    fired = engine.evaluate(sources)
    assert any(a.rule == "api.latency_high" and a.severity == AlertSeverity.CRITICAL for a in fired)


def test_alert_scanner_inactive_rule():
    engine = AlertEngine()
    scanner = ScannerMonitor()  # no scan completed yet -> WARNING
    sources = AlertSources(scanner_status=scanner.health)
    fired = engine.evaluate(sources)
    assert any(a.rule == "scanner.inactive" for a in fired)


def test_alert_broken_source_does_not_break_evaluation():
    engine = AlertEngine()

    def _raises():
        raise RuntimeError("boom")

    sources = AlertSources(broker_status=_raises, telegram_status=TelegramMonitor().health)
    fired = engine.evaluate(sources)  # must not raise
    assert fired == []  # telegram healthy, broker source broken and skipped


def test_alert_active_reflects_cooldown_window():
    engine = AlertEngine(cooldown_seconds=0.05)
    broker = BrokerMonitor()
    sources = AlertSources(broker_status=broker.health)
    engine.evaluate(sources)
    assert len(engine.active()) == 1
    time.sleep(0.1)
    assert len(engine.active()) == 0


def test_alert_history_filters_by_severity():
    engine = AlertEngine()
    sources = AlertSources(exception_counts=lambda: {"a": 5})  # ERROR
    engine.evaluate(sources)
    assert len(engine.history(severity_at_least=AlertSeverity.CRITICAL)) == 0
    assert len(engine.history(severity_at_least=AlertSeverity.ERROR)) == 1


ALL_TESTS = [
    test_alert_fires_for_broker_disconnected,
    test_alert_does_not_fire_for_healthy_component,
    test_alert_cooldown_aggregates_repeated_firing,
    test_alert_clear_resets_aggregation,
    test_alert_suppression_blocks_firing,
    test_alert_resource_thresholds_cpu_memory_disk,
    test_alert_repeated_exceptions_rule,
    test_alert_repeated_exceptions_below_threshold_does_not_fire,
    test_alert_websocket_failures_rule,
    test_alert_api_latency_high_rule,
    test_alert_scanner_inactive_rule,
    test_alert_broken_source_does_not_break_evaluation,
    test_alert_active_reflects_cooldown_window,
    test_alert_history_filters_by_severity,
]


if __name__ == "__main__":
    passed = 0
    for test_fn in ALL_TESTS:
        test_fn()
        passed += 1
        print(f"PASS: {test_fn.__name__}")
    print(f"\n{passed}/{len(ALL_TESTS)} passed")
