"""
scripts/production/tests/test_part10_phase3.py -- unit tests for
Part 10 Phase 3: websocket_monitor, api_monitor, dashboard_monitor.
------------------------------------------------------------------------------
Plain assert-based, no pytest dependency (matches test_part10_phase1.py
and test_part10_phase2.py). Fully offline / in-process -- does not
import FastAPI/Starlette, so APIMonitoringMiddleware (which needs
starlette) is not exercised here; see MERGE_SUMMARY for the same
limitation affecting app/backend's own tests.

Run with: python scripts/production/tests/test_part10_phase3.py
"""

import os
import sys
import time

_here = os.path.dirname(os.path.abspath(__file__))
_scripts_dir = os.path.dirname(os.path.dirname(_here))
sys.path.insert(0, _scripts_dir)

from production.websocket_monitor import WebSocketMonitor
from production.api_monitor import APIMonitor
from production.dashboard_monitor import DashboardMonitor
from production.health_states import HealthState


# --------------------------------------------------------------------
# websocket_monitor
# --------------------------------------------------------------------

def test_websocket_monitor_tracks_connect_disconnect():
    m = WebSocketMonitor()
    m.record_connect(client_key=1)
    m.record_connect(client_key=2)
    snap = m.snapshot()
    assert snap.connected_clients == 2
    assert snap.active_clients == 2
    m.record_disconnect(client_key=1)
    snap = m.snapshot()
    assert snap.active_clients == 1
    assert snap.disconnected_clients == 1


def test_websocket_monitor_active_clients_never_negative():
    m = WebSocketMonitor()
    m.record_disconnect(client_key=99)  # disconnect with no matching connect
    assert m.snapshot().active_clients == 0


def test_websocket_monitor_connection_duration_recorded():
    m = WebSocketMonitor()
    m.record_connect(client_key=1)
    time.sleep(0.01)
    m.record_disconnect(client_key=1)
    snap = m.snapshot()
    assert snap.avg_connection_duration_seconds is not None
    assert snap.avg_connection_duration_seconds >= 0.01


def test_websocket_monitor_broadcast_stats():
    m = WebSocketMonitor()
    m.record_broadcast(duration_ms=10.0, recipients=5, failures=1, dropped=1)
    m.record_broadcast(duration_ms=20.0, recipients=5, failures=0, dropped=0)
    snap = m.snapshot()
    assert snap.broadcast_count == 2
    assert snap.broadcast_failures == 1
    assert snap.dropped_messages == 1
    assert snap.avg_broadcast_time_ms == 15.0
    assert snap.last_broadcast_at is not None


def test_websocket_monitor_reconnect_tracking():
    m = WebSocketMonitor()
    m.record_reconnect_attempt(success=True)
    m.record_reconnect_attempt(success=False)
    snap = m.snapshot()
    assert snap.reconnect_count == 2
    assert snap.reconnect_success == 1


def test_websocket_monitor_healthy_by_default():
    m = WebSocketMonitor()
    assert m.health().state == HealthState.HEALTHY


def test_websocket_monitor_degraded_on_broadcast_failures():
    m = WebSocketMonitor()
    for _ in range(2):
        m.record_broadcast(duration_ms=5.0, recipients=1, failures=1, dropped=1)
    for _ in range(3):
        m.record_broadcast(duration_ms=5.0, recipients=1, failures=0, dropped=0)
    health = m.health()
    assert health.state == HealthState.DEGRADED


def test_websocket_monitor_warning_on_slow_broadcasts():
    m = WebSocketMonitor(slow_broadcast_ms=100.0)
    m.record_broadcast(duration_ms=500.0, recipients=1)
    health = m.health()
    assert health.state == HealthState.WARNING


def test_websocket_monitor_status_shape_for_health_aggregator():
    m = WebSocketMonitor()
    m.record_connect(client_key=1)
    status = m.status()
    assert status["active_clients"] == 1


# --------------------------------------------------------------------
# api_monitor
# --------------------------------------------------------------------

def test_api_monitor_records_success_and_failure():
    m = APIMonitor()
    m.request_started("/api/health")
    m.record_request("/api/health", duration_ms=50.0, success=True)
    m.request_started("/api/health")
    m.record_request("/api/health", duration_ms=80.0, success=False)
    snap = m.snapshot("/api/health")
    assert snap.request_count == 2
    assert snap.success_count == 1
    assert snap.failure_count == 1
    assert snap.avg_response_time_ms == 65.0


def test_api_monitor_concurrent_requests_tracked():
    m = APIMonitor()
    m.request_started("/api/scanner/status")
    m.request_started("/api/scanner/status")
    snap = m.snapshot("/api/scanner/status")
    assert snap.concurrent_requests == 2
    m.record_request("/api/scanner/status", duration_ms=10.0, success=True)
    snap = m.snapshot("/api/scanner/status")
    assert snap.concurrent_requests == 1


def test_api_monitor_slow_requests_counted():
    m = APIMonitor(slow_request_ms=100.0)
    m.request_started("/api/x")
    m.record_request("/api/x", duration_ms=500.0, success=True)
    assert m.snapshot("/api/x").slow_requests == 1


def test_api_monitor_rejected_validation_rate_limit_events():
    m = APIMonitor()
    m.record_rejected("/api/x")
    m.record_validation_error("/api/x")
    m.record_rate_limited("/api/x")
    snap = m.snapshot("/api/x")
    assert snap.rejected_requests == 1
    assert snap.validation_errors == 1
    assert snap.rate_limit_events == 1


def test_api_monitor_unknown_endpoint_returns_none():
    m = APIMonitor()
    assert m.snapshot("/never/called") is None


def test_api_monitor_totals_roll_up_across_endpoints():
    m = APIMonitor()
    for ep in ("/a", "/b"):
        m.request_started(ep)
        m.record_request(ep, duration_ms=10.0, success=True)
    totals = m.totals()
    assert totals["request_count"] == 2


def test_api_monitor_healthy_by_default():
    m = APIMonitor()
    assert m.health().state == HealthState.HEALTHY


def test_api_monitor_degraded_on_high_failure_rate():
    m = APIMonitor()
    for _ in range(8):
        m.request_started("/a")
        m.record_request("/a", duration_ms=10.0, success=True)
    for _ in range(2):
        m.request_started("/a")
        m.record_request("/a", duration_ms=10.0, success=False)
    assert m.health().state == HealthState.DEGRADED


def test_api_monitor_warning_when_requests_rejected():
    m = APIMonitor()
    m.request_started("/a")
    m.record_request("/a", duration_ms=10.0, success=True)
    m.record_rejected("/a")
    assert m.health().state == HealthState.WARNING


# --------------------------------------------------------------------
# dashboard_monitor
# --------------------------------------------------------------------

def test_dashboard_monitor_uses_own_counters_when_set():
    api = APIMonitor()
    ws = WebSocketMonitor()
    d = DashboardMonitor(api_monitor=api, ws_monitor=ws)
    d.record_request()
    d.record_request()
    d.record_error()
    snap = d.snapshot()
    assert snap.dashboard_requests == 2
    assert snap.dashboard_errors == 1


def test_dashboard_monitor_falls_back_to_api_monitor_totals():
    api = APIMonitor()
    api.request_started("/api/health")
    api.record_request("/api/health", duration_ms=10.0, success=True)
    ws = WebSocketMonitor()
    d = DashboardMonitor(api_monitor=api, ws_monitor=ws)
    # record_request()/record_error() never called -- should fall back
    snap = d.snapshot()
    assert snap.dashboard_requests == 1


def test_dashboard_monitor_reports_websocket_client_count():
    api = APIMonitor()
    ws = WebSocketMonitor()
    ws.record_connect(client_key=1)
    d = DashboardMonitor(api_monitor=api, ws_monitor=ws)
    assert d.snapshot().websocket_clients == 1


def test_dashboard_monitor_session_tracking():
    d = DashboardMonitor(api_monitor=APIMonitor(), ws_monitor=WebSocketMonitor())
    d.session_opened()
    d.session_opened()
    d.session_closed()
    assert d.snapshot().active_sessions == 1


def test_dashboard_monitor_offline_when_unavailable():
    d = DashboardMonitor(api_monitor=APIMonitor(), ws_monitor=WebSocketMonitor())
    d.set_available(False)
    assert d.health().state == HealthState.OFFLINE


def test_dashboard_monitor_refresh_time_recorded():
    d = DashboardMonitor(api_monitor=APIMonitor(), ws_monitor=WebSocketMonitor())
    d.record_refresh(0.5)
    d.record_refresh(1.5)
    snap = d.snapshot()
    assert snap.avg_refresh_time_seconds == 1.0
    assert snap.last_refresh_at is not None


def test_dashboard_monitor_warning_on_slow_refresh():
    d = DashboardMonitor(api_monitor=APIMonitor(), ws_monitor=WebSocketMonitor(), slow_refresh_seconds=1.0)
    d.record_refresh(5.0)
    assert d.health().state == HealthState.WARNING


ALL_TESTS = [
    test_websocket_monitor_tracks_connect_disconnect,
    test_websocket_monitor_active_clients_never_negative,
    test_websocket_monitor_connection_duration_recorded,
    test_websocket_monitor_broadcast_stats,
    test_websocket_monitor_reconnect_tracking,
    test_websocket_monitor_healthy_by_default,
    test_websocket_monitor_degraded_on_broadcast_failures,
    test_websocket_monitor_warning_on_slow_broadcasts,
    test_websocket_monitor_status_shape_for_health_aggregator,
    test_api_monitor_records_success_and_failure,
    test_api_monitor_concurrent_requests_tracked,
    test_api_monitor_slow_requests_counted,
    test_api_monitor_rejected_validation_rate_limit_events,
    test_api_monitor_unknown_endpoint_returns_none,
    test_api_monitor_totals_roll_up_across_endpoints,
    test_api_monitor_healthy_by_default,
    test_api_monitor_degraded_on_high_failure_rate,
    test_api_monitor_warning_when_requests_rejected,
    test_dashboard_monitor_uses_own_counters_when_set,
    test_dashboard_monitor_falls_back_to_api_monitor_totals,
    test_dashboard_monitor_reports_websocket_client_count,
    test_dashboard_monitor_session_tracking,
    test_dashboard_monitor_offline_when_unavailable,
    test_dashboard_monitor_refresh_time_recorded,
    test_dashboard_monitor_warning_on_slow_refresh,
]


if __name__ == "__main__":
    passed = 0
    for test_fn in ALL_TESTS:
        test_fn()
        passed += 1
        print(f"PASS: {test_fn.__name__}")
    print(f"\n{passed}/{len(ALL_TESTS)} passed")
