"""
scripts/production/tests/test_part10_phase1.py -- unit tests for
Part 10 Phase 1: health_states, system_monitor, health_registry.
------------------------------------------------------------------------------
Plain assert-based, no pytest dependency (matches test_phase1_production.py
and friends). Fully offline / in-process.

Run with: python scripts/production/tests/test_part10_phase1.py
"""

import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
_scripts_dir = os.path.dirname(os.path.dirname(_here))
sys.path.insert(0, _scripts_dir)

from production.health_states import (
    HealthState, ComponentHealth, combine, combine_components, state_from_pct,
)
from production.system_monitor import SystemMonitor, SystemSnapshot
from production.resource_monitor import ResourceMonitor
from production.task_manager import TaskManager
from production.health_registry import HealthRegistry, KNOWN_COMPONENTS


# --------------------------------------------------------------------
# health_states
# --------------------------------------------------------------------

def test_health_state_ordering_worst_wins():
    assert combine([HealthState.HEALTHY, HealthState.WARNING]) == HealthState.WARNING
    assert combine([HealthState.CRITICAL, HealthState.HEALTHY, HealthState.OFFLINE]) == HealthState.OFFLINE
    assert combine([]) == HealthState.HEALTHY


def test_str_of_health_state_is_the_name():
    assert str(HealthState.DEGRADED) == "DEGRADED"


def test_combine_components_uses_component_states():
    components = [
        ComponentHealth(name="a", state=HealthState.HEALTHY, message="ok"),
        ComponentHealth(name="b", state=HealthState.CRITICAL, message="bad"),
    ]
    assert combine_components(components) == HealthState.CRITICAL


def test_component_health_to_dict_shape():
    c = ComponentHealth(name="scanner", state=HealthState.WARNING, message="slow", metrics={"x": 1})
    d = c.to_dict()
    assert d["name"] == "scanner"
    assert d["state"] == "WARNING"
    assert d["metrics"] == {"x": 1}
    assert "checked_at" in d


def test_state_from_pct_thresholds():
    assert state_from_pct(10.0) == HealthState.HEALTHY
    assert state_from_pct(80.0) == HealthState.WARNING
    assert state_from_pct(92.0) == HealthState.DEGRADED
    assert state_from_pct(99.0) == HealthState.CRITICAL


def test_state_from_pct_none_is_warning_not_healthy():
    assert state_from_pct(None) == HealthState.WARNING


# --------------------------------------------------------------------
# system_monitor
# --------------------------------------------------------------------

def test_system_monitor_health_healthy_when_no_task_manager():
    monitor = SystemMonitor(resource_monitor=ResourceMonitor())
    health = monitor.health()
    assert health.name == "system"
    assert isinstance(health.state, HealthState)
    assert "cpu_pct" in health.metrics


def test_system_monitor_sample_includes_gauges():
    rm = ResourceMonitor()
    rm.register_gauge("queue_size", lambda: 42)
    rm.register_gauge("api_latency_ms", lambda: 123.0)
    monitor = SystemMonitor(resource_monitor=rm)
    snap = monitor.sample()
    assert isinstance(snap, SystemSnapshot)
    assert snap.queue_length == 42
    assert snap.api_latency_ms == 123.0


def test_system_monitor_reflects_task_manager_worker_status():
    tm = TaskManager()

    def worker(stop_event, heartbeat):
        heartbeat()
        stop_event.wait(0.2)

    tm.register("w1", worker)
    tm.start("w1")
    import time
    time.sleep(0.02)
    try:
        monitor = SystemMonitor(resource_monitor=ResourceMonitor(), task_manager=tm)
        snap = monitor.sample()
        assert snap.running_tasks == 1
        assert snap.worker_status["w1"] == "running"
    finally:
        tm.stop("w1", timeout=2.0)


def test_system_monitor_degraded_when_worker_crashed():
    tm = TaskManager()

    def crash_worker(stop_event, heartbeat):
        raise RuntimeError("boom")

    tm.register("crasher", crash_worker, restart_on_crash=False)
    tm.start("crasher")
    import time
    time.sleep(0.05)

    monitor = SystemMonitor(resource_monitor=ResourceMonitor(), task_manager=tm)
    health = monitor.health()
    assert health.state >= HealthState.DEGRADED


# --------------------------------------------------------------------
# health_registry
# --------------------------------------------------------------------

def test_unregistered_known_component_is_offline_not_monitored():
    registry = HealthRegistry()
    report = registry.check_all()
    assert set(KNOWN_COMPONENTS).issubset(report.keys())
    assert report["broker"].state == HealthState.OFFLINE
    assert report["broker"].message == "not monitored"


def test_registered_provider_result_is_used():
    registry = HealthRegistry()
    registry.register("broker", lambda: ComponentHealth(name="broker", state=HealthState.HEALTHY, message="connected"))
    result = registry.check("broker")
    assert result.state == HealthState.HEALTHY
    assert result.message == "connected"


def test_provider_exception_becomes_offline_not_a_crash():
    registry = HealthRegistry()

    def broken():
        raise RuntimeError("provider bug")

    registry.register("telegram", broken)
    result = registry.check("telegram")
    assert result.state == HealthState.OFFLINE
    assert "provider bug" in result.message


def test_provider_wrong_return_type_becomes_offline():
    registry = HealthRegistry()
    registry.register("api", lambda: {"not": "a ComponentHealth"})
    result = registry.check("api")
    assert result.state == HealthState.OFFLINE


def test_overall_state_is_worst_of_all_components():
    registry = HealthRegistry()
    for name in KNOWN_COMPONENTS:
        registry.register(name, lambda n=name: ComponentHealth(name=n, state=HealthState.HEALTHY, message="ok"))
    registry.register("broker", lambda: ComponentHealth(name="broker", state=HealthState.CRITICAL, message="down"))
    assert registry.overall_state() == HealthState.CRITICAL


def test_overall_state_is_offline_when_any_known_component_unmonitored():
    registry = HealthRegistry()
    registry.register("scanner", lambda: ComponentHealth(name="scanner", state=HealthState.HEALTHY, message="ok"))
    # every other KNOWN_COMPONENTS entry is left unregistered -> OFFLINE,
    # which must dominate a single HEALTHY registration.
    assert registry.overall_state() == HealthState.OFFLINE


def test_report_shape():
    registry = HealthRegistry()
    registry.register("scanner", lambda: ComponentHealth(name="scanner", state=HealthState.HEALTHY, message="ok"))
    report = registry.report()
    assert "overall_state" in report
    assert "components" in report
    assert report["components"]["scanner"]["state"] == "HEALTHY"


def test_unregister_reverts_to_not_monitored():
    registry = HealthRegistry()
    registry.register("broker", lambda: ComponentHealth(name="broker", state=HealthState.HEALTHY, message="ok"))
    registry.unregister("broker")
    result = registry.check("broker")
    assert result.state == HealthState.OFFLINE
    assert result.message == "not monitored"


ALL_TESTS = [
    test_health_state_ordering_worst_wins,
    test_str_of_health_state_is_the_name,
    test_combine_components_uses_component_states,
    test_component_health_to_dict_shape,
    test_state_from_pct_thresholds,
    test_state_from_pct_none_is_warning_not_healthy,
    test_system_monitor_health_healthy_when_no_task_manager,
    test_system_monitor_sample_includes_gauges,
    test_system_monitor_reflects_task_manager_worker_status,
    test_system_monitor_degraded_when_worker_crashed,
    test_unregistered_known_component_is_offline_not_monitored,
    test_registered_provider_result_is_used,
    test_provider_exception_becomes_offline_not_a_crash,
    test_provider_wrong_return_type_becomes_offline,
    test_overall_state_is_worst_of_all_components,
    test_overall_state_is_offline_when_any_known_component_unmonitored,
    test_report_shape,
    test_unregister_reverts_to_not_monitored,
]


if __name__ == "__main__":
    passed = 0
    for test_fn in ALL_TESTS:
        test_fn()
        passed += 1
        print(f"PASS: {test_fn.__name__}")
    print(f"\n{passed}/{len(ALL_TESTS)} passed")
