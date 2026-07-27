"""
scripts/production/tests/test_part10_phase6.py -- unit tests for
Part 10 Phase 6: historical_metrics, service_registry,
performance_summary, and alert_engine.cleanup_history.
------------------------------------------------------------------------------
Plain assert-based, no pytest dependency. Fully offline / in-process.

Run with: python scripts/production/tests/test_part10_phase6.py
"""

import os
import sys
import time

_here = os.path.dirname(os.path.abspath(__file__))
_scripts_dir = os.path.dirname(os.path.dirname(_here))
sys.path.insert(0, _scripts_dir)

from production.historical_metrics import HistoricalMetricsStore, ONE_MINUTE, FIVE_MINUTES
from production.service_registry import ServiceRegistry
from production.performance_summary import generate_performance_summary
from production.alert_engine import AlertEngine, AlertSources
from production.health_registry import HealthRegistry
from production.task_manager import TaskManager
from production.broker_monitor import BrokerMonitor
from production.telegram_monitor import TelegramMonitor
from production.scanner_monitor import ScannerMonitor
from production.api_monitor import APIMonitor
from production.dashboard_monitor import DashboardMonitor
from production.websocket_monitor import WebSocketMonitor


# -- historical_metrics ---------------------------------------------

def test_historical_metrics_record_and_rollup():
    store = HistoricalMetricsStore()
    now = time.time()
    store.record("scan_duration", 1.0, at=now)
    store.record("scan_duration", 3.0, at=now)  # same 1-minute bucket
    buckets = store.rollup("scan_duration", ONE_MINUTE)
    assert len(buckets) == 1
    assert buckets[0].count == 2
    assert buckets[0].average == 2.0
    assert buckets[0].minimum == 1.0
    assert buckets[0].maximum == 3.0


def test_historical_metrics_separate_buckets_across_time():
    store = HistoricalMetricsStore()
    now = time.time()
    store.record("x", 1.0, at=now - 200)  # different 1-minute bucket
    store.record("x", 2.0, at=now)
    buckets = store.rollup("x", ONE_MINUTE)
    assert len(buckets) == 2


def test_historical_metrics_all_rollups_has_five_granularities():
    store = HistoricalMetricsStore()
    store.record("y", 5.0)
    rollups = store.all_rollups("y")
    assert set(rollups.keys()) == {"1_minute", "5_minutes", "15_minutes", "1_hour", "daily"}
    for buckets in rollups.values():
        assert len(buckets) == 1


def test_historical_metrics_unknown_metric_returns_empty():
    store = HistoricalMetricsStore()
    assert store.rollup("never_recorded", ONE_MINUTE) == []


def test_historical_metrics_lookback_filters_old_samples():
    store = HistoricalMetricsStore()
    now = time.time()
    store.record("z", 1.0, at=now - 10000)
    store.record("z", 2.0, at=now)
    recent = store.rollup("z", FIVE_MINUTES, lookback_seconds=60)
    assert len(recent) == 1
    assert recent[0].average == 2.0


def test_historical_metrics_cleanup_drops_old_samples():
    store = HistoricalMetricsStore()
    now = time.time()
    store.record("old_metric", 1.0, at=now - 1000)
    store.record("old_metric", 2.0, at=now)
    dropped = store.cleanup(retention_seconds=500)
    assert dropped == 1
    assert store.sample_count("old_metric") == 1


def test_historical_metrics_metric_names():
    store = HistoricalMetricsStore()
    store.record("a", 1.0)
    store.record("b", 2.0)
    assert store.metric_names() == ["a", "b"]


def test_historical_metrics_sample_count_total():
    store = HistoricalMetricsStore()
    store.record("a", 1.0)
    store.record("a", 2.0)
    store.record("b", 3.0)
    assert store.sample_count() == 3


# -- service_registry ---------------------------------------------

def test_service_registry_register_and_dependency_map():
    registry = ServiceRegistry()
    registry.register_component("dashboard", category="monitor", depends_on=["websocket", "fastapi"])
    registry.register_component("websocket", category="monitor")
    assert registry.dependency_map()["dashboard"] == ["websocket", "fastapi"]
    assert registry.dependency_map()["websocket"] == []


def test_service_registry_unregister():
    registry = ServiceRegistry()
    registry.register_component("scanner", category="monitor")
    registry.unregister_component("scanner")
    assert "scanner" not in registry.dependency_map()


def test_service_registry_register_known_components_wires_health_registry():
    health_registry = HealthRegistry()
    registry = ServiceRegistry(health_registry=health_registry)
    telegram = TelegramMonitor()
    registry.register_known_components(telegram_status=telegram.health)
    assert "telegram" in health_registry.registered_names()
    assert registry.dependency_map()["telegram"] == []


def test_service_registry_known_component_dependency_edges():
    registry = ServiceRegistry(health_registry=HealthRegistry())
    registry.register_known_components(
        api_status=APIMonitor().health, dashboard_status=DashboardMonitor(
            api_monitor=APIMonitor(), ws_monitor=WebSocketMonitor()).health,
    )
    deps = registry.dependency_map()
    assert set(deps["fastapi"]) == {"scanner", "broker", "telegram"}
    assert set(deps["dashboard"]) == {"websocket", "fastapi"}


def test_service_registry_runtime_status_combines_map_and_health():
    health_registry = HealthRegistry()
    registry = ServiceRegistry(health_registry=health_registry)
    broker = BrokerMonitor()  # OFFLINE by default
    registry.register_known_components(broker_status=broker.health)
    status = registry.runtime_status()
    assert status["broker"]["state"] == "OFFLINE"
    assert status["broker"]["depends_on"] == []


def test_service_registry_runtime_status_without_health_provider():
    registry = ServiceRegistry()  # no health_registry
    registry.register_component("custom_thing", category="worker")
    status = registry.runtime_status()
    assert status["custom_thing"]["state"] == "NOT_MONITORED"


def test_service_registry_worker_and_background_task_registry_share_data():
    tm = TaskManager()
    tm.register("worker_a", target=lambda stop_event, heartbeat: None)
    registry = ServiceRegistry(task_manager=tm)
    assert registry.worker_registry() == registry.background_task_registry()
    assert registry.worker_registry()[0]["name"] == "worker_a"


def test_service_registry_worker_registry_empty_without_task_manager():
    registry = ServiceRegistry()
    assert registry.worker_registry() == []


def test_service_registry_monitoring_registry_lists_registered_names():
    health_registry = HealthRegistry()
    registry = ServiceRegistry(health_registry=health_registry)
    registry.register_known_components(scanner_status=ScannerMonitor().health)
    assert "scanner" in registry.monitoring_registry()


# -- performance_summary ---------------------------------------------

def test_performance_summary_all_none_with_no_inputs():
    summary = generate_performance_summary()
    d = summary.to_dict()
    assert all(v is None for v in d.values())


def test_performance_summary_scanner_and_dashboard():
    scanner = ScannerMonitor()
    scanner.scan_started()
    scanner.scan_completed()
    dashboard = DashboardMonitor(api_monitor=APIMonitor(), ws_monitor=WebSocketMonitor())
    dashboard.record_refresh(2.0)

    summary = generate_performance_summary(scanner_monitor=scanner, dashboard_monitor=dashboard)
    assert summary.average_scanner_duration_seconds is not None
    assert summary.average_dashboard_duration_seconds == 2.0


def test_performance_summary_replay_and_analytics_from_api_paths():
    api = APIMonitor()
    api.request_started("/api/replay/journal")
    api.record_request("/api/replay/journal", duration_ms=100.0, success=True)
    api.request_started("/api/analytics/performance")
    api.record_request("/api/analytics/performance", duration_ms=200.0, success=True)
    api.request_started("/api/health")
    api.record_request("/api/health", duration_ms=10.0, success=True)

    summary = generate_performance_summary(api_monitor=api)
    assert summary.average_replay_duration_ms == 100.0
    assert summary.average_analytics_duration_ms == 200.0
    # overall API average includes all three endpoints
    assert summary.average_api_duration_ms == round((100.0 + 200.0 + 10.0) / 3, 2)


# -- alert_engine cleanup_history (Phase 6) ---------------------------

def test_alert_engine_cleanup_history_drops_old_entries():
    engine = AlertEngine(cooldown_seconds=0.01)
    sources = AlertSources(broker_status=BrokerMonitor().health)
    engine.evaluate(sources)
    time.sleep(0.05)
    dropped = engine.cleanup_history(max_age_seconds=0.02)
    assert dropped == 1
    assert engine.history() == []


def test_alert_engine_cleanup_history_keeps_recent_entries():
    engine = AlertEngine()
    sources = AlertSources(broker_status=BrokerMonitor().health)
    engine.evaluate(sources)
    dropped = engine.cleanup_history(max_age_seconds=3600)
    assert dropped == 0
    assert len(engine.history()) == 1


ALL_TESTS = [
    test_historical_metrics_record_and_rollup,
    test_historical_metrics_separate_buckets_across_time,
    test_historical_metrics_all_rollups_has_five_granularities,
    test_historical_metrics_unknown_metric_returns_empty,
    test_historical_metrics_lookback_filters_old_samples,
    test_historical_metrics_cleanup_drops_old_samples,
    test_historical_metrics_metric_names,
    test_historical_metrics_sample_count_total,
    test_service_registry_register_and_dependency_map,
    test_service_registry_unregister,
    test_service_registry_register_known_components_wires_health_registry,
    test_service_registry_known_component_dependency_edges,
    test_service_registry_runtime_status_combines_map_and_health,
    test_service_registry_runtime_status_without_health_provider,
    test_service_registry_worker_and_background_task_registry_share_data,
    test_service_registry_worker_registry_empty_without_task_manager,
    test_service_registry_monitoring_registry_lists_registered_names,
    test_performance_summary_all_none_with_no_inputs,
    test_performance_summary_scanner_and_dashboard,
    test_performance_summary_replay_and_analytics_from_api_paths,
    test_alert_engine_cleanup_history_drops_old_entries,
    test_alert_engine_cleanup_history_keeps_recent_entries,
]


if __name__ == "__main__":
    passed = 0
    for test_fn in ALL_TESTS:
        test_fn()
        passed += 1
        print(f"PASS: {test_fn.__name__}")
    print(f"\n{passed}/{len(ALL_TESTS)} passed")
