"""
scripts/production/tests/test_part10_phase2.py -- unit tests for
Part 10 Phase 2: metrics_utils, scanner_monitor, broker_monitor,
telegram_monitor.
------------------------------------------------------------------------------
Plain assert-based, no pytest dependency. Fully offline / in-process.

Run with: python scripts/production/tests/test_part10_phase2.py
"""

import os
import sys
import time

_here = os.path.dirname(os.path.abspath(__file__))
_scripts_dir = os.path.dirname(os.path.dirname(_here))
sys.path.insert(0, _scripts_dir)

from production.metrics_utils import RollingWindow
from production.scanner_monitor import ScannerMonitor
from production.broker_monitor import BrokerMonitor
from production.telegram_monitor import TelegramMonitor
from production.health_states import HealthState


# --------------------------------------------------------------------
# metrics_utils
# --------------------------------------------------------------------

def test_rolling_window_basic_stats():
    w = RollingWindow(max_samples=100)
    for v in [1, 2, 3, 4, 5]:
        w.record(v)
    s = w.stats()
    assert s.count == 5
    assert s.average == 3.0
    assert s.maximum == 5
    assert s.minimum == 1
    assert s.latest == 5


def test_rolling_window_respects_capacity():
    w = RollingWindow(max_samples=3)
    for v in [1, 2, 3, 4, 5]:
        w.record(v)
    s = w.stats()
    assert s.count == 3
    assert s.minimum == 3  # 1 and 2 evicted


def test_rolling_window_empty_stats_are_none():
    w = RollingWindow()
    s = w.stats()
    assert s.count == 0
    assert s.average is None
    assert s.p95 is None


def test_rolling_window_p95_reasonable():
    w = RollingWindow(max_samples=1000)
    for v in range(1, 101):  # 1..100
        w.record(v)
    s = w.stats()
    assert 94 <= s.p95 <= 100


def test_rolling_window_trend_buckets():
    w = RollingWindow(max_samples=100)
    for v in range(10):
        w.record(v)
    trend = w.trend(buckets=5)
    assert len(trend) <= 5
    assert trend[0] < trend[-1]  # increasing values -> increasing bucket averages


# --------------------------------------------------------------------
# scanner_monitor
# --------------------------------------------------------------------

def test_scanner_monitor_records_completed_scan():
    m = ScannerMonitor()
    m.scan_started()
    time.sleep(0.01)
    m.scan_completed(alert_count=3)
    snap = m.snapshot()
    assert snap.scan_count == 1
    assert snap.alerts_today == 3
    assert snap.last_scan_duration_seconds is not None
    assert snap.last_scan_duration_seconds >= 0.01


def test_scanner_monitor_health_warning_before_any_scan():
    m = ScannerMonitor()
    health = m.health()
    assert health.state == HealthState.WARNING
    assert "no scan has completed" in health.message


def test_scanner_monitor_health_offline_after_long_inactivity():
    m = ScannerMonitor(no_activity_seconds=0.05)
    m.scan_started()
    m.scan_completed()
    time.sleep(0.1)
    health = m.health()
    assert health.state == HealthState.OFFLINE


def test_scanner_monitor_health_critical_when_stalled():
    m = ScannerMonitor(stall_seconds=0.05)
    m.scan_started()
    time.sleep(0.1)
    health = m.health()
    assert health.state == HealthState.CRITICAL


def test_scanner_monitor_success_pct_accounts_for_failures():
    m = ScannerMonitor()
    for _ in range(4):
        m.scan_started()
        m.scan_completed()
    m.scan_started()
    m.scan_failed()
    snap = m.snapshot()
    assert snap.success_pct == 80.0
    assert snap.failed_scans == 1


def test_scanner_monitor_reset_daily_counters_only_resets_alerts():
    m = ScannerMonitor()
    m.scan_started()
    m.scan_completed(alert_count=5)
    m.reset_daily_counters()
    snap = m.snapshot()
    assert snap.alerts_today == 0
    assert snap.scan_count == 1  # not reset


def test_scanner_monitor_next_scan_at_is_caller_supplied():
    m = ScannerMonitor()
    m.set_next_scan_at(12345.0)
    assert m.snapshot().next_scan_at == 12345.0


# --------------------------------------------------------------------
# broker_monitor
# --------------------------------------------------------------------

def test_broker_monitor_offline_when_not_connected():
    m = BrokerMonitor()
    health = m.health()
    assert health.state == HealthState.OFFLINE


def test_broker_monitor_healthy_when_connected_and_fine():
    m = BrokerMonitor()
    m.record_connect()
    m.set_token_expiry(time.time() + 10000)
    m.record_api_call(50.0, success=True)
    health = m.health()
    assert health.state == HealthState.HEALTHY


def test_broker_monitor_critical_when_token_expired():
    m = BrokerMonitor()
    m.record_connect()
    m.set_token_expiry(time.time() - 10)
    health = m.health()
    assert health.state == HealthState.CRITICAL


def test_broker_monitor_warning_when_token_expiring_soon():
    m = BrokerMonitor(token_expiry_warning_seconds=600)
    m.record_connect()
    m.set_token_expiry(time.time() + 100)
    health = m.health()
    assert health.state == HealthState.WARNING


def test_broker_monitor_degraded_on_high_failure_rate():
    m = BrokerMonitor()
    m.record_connect()
    m.set_token_expiry(time.time() + 10000)
    for _ in range(3):
        m.record_api_call(50.0, success=True)
    for _ in range(2):
        m.record_api_call(50.0, success=False)
    health = m.health()
    assert health.state == HealthState.DEGRADED


def test_broker_monitor_degraded_when_rate_limited():
    m = BrokerMonitor()
    m.record_connect()
    m.set_token_expiry(time.time() + 10000)
    m.set_rate_limit_status(True, remaining=0)
    health = m.health()
    assert health.state == HealthState.DEGRADED


def test_broker_monitor_snapshot_tracks_reconnects():
    m = BrokerMonitor()
    m.record_reconnect_attempt()
    m.record_reconnect_attempt()
    assert m.snapshot().reconnect_attempts == 2


# --------------------------------------------------------------------
# telegram_monitor
# --------------------------------------------------------------------

def test_telegram_monitor_healthy_by_default():
    m = TelegramMonitor()
    health = m.health()
    assert health.state == HealthState.HEALTHY


def test_telegram_monitor_offline_when_unreachable():
    m = TelegramMonitor()
    m.record_unreachable()
    health = m.health()
    assert health.state == HealthState.OFFLINE


def test_telegram_monitor_degraded_on_high_failure_rate():
    m = TelegramMonitor()
    for _ in range(2):
        m.record_sent(100.0)
    for _ in range(3):
        m.record_failure()
    health = m.health()
    assert health.state == HealthState.DEGRADED


def test_telegram_monitor_warning_on_many_retries():
    m = TelegramMonitor()
    m.record_sent(100.0)
    for _ in range(6):
        m.record_retry()
    health = m.health()
    assert health.state == HealthState.WARNING


def test_telegram_monitor_snapshot_tracks_latency():
    m = TelegramMonitor()
    m.record_sent(100.0)
    m.record_sent(200.0)
    snap = m.snapshot()
    assert snap.messages_sent == 2
    assert snap.delivery_latency_ms_avg == 150.0


ALL_TESTS = [
    test_rolling_window_basic_stats,
    test_rolling_window_respects_capacity,
    test_rolling_window_empty_stats_are_none,
    test_rolling_window_p95_reasonable,
    test_rolling_window_trend_buckets,
    test_scanner_monitor_records_completed_scan,
    test_scanner_monitor_health_warning_before_any_scan,
    test_scanner_monitor_health_offline_after_long_inactivity,
    test_scanner_monitor_health_critical_when_stalled,
    test_scanner_monitor_success_pct_accounts_for_failures,
    test_scanner_monitor_reset_daily_counters_only_resets_alerts,
    test_scanner_monitor_next_scan_at_is_caller_supplied,
    test_broker_monitor_offline_when_not_connected,
    test_broker_monitor_healthy_when_connected_and_fine,
    test_broker_monitor_critical_when_token_expired,
    test_broker_monitor_warning_when_token_expiring_soon,
    test_broker_monitor_degraded_on_high_failure_rate,
    test_broker_monitor_degraded_when_rate_limited,
    test_broker_monitor_snapshot_tracks_reconnects,
    test_telegram_monitor_healthy_by_default,
    test_telegram_monitor_offline_when_unreachable,
    test_telegram_monitor_degraded_on_high_failure_rate,
    test_telegram_monitor_warning_on_many_retries,
    test_telegram_monitor_snapshot_tracks_latency,
]


if __name__ == "__main__":
    passed = 0
    for test_fn in ALL_TESTS:
        test_fn()
        passed += 1
        print(f"PASS: {test_fn.__name__}")
    print(f"\n{passed}/{len(ALL_TESTS)} passed")
