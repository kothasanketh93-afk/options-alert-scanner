"""
Unit tests for the Phase 1 backend services. These never touch git or
the real state branch -- they monkeypatch state_reader.get_state_for_date
with fixture dicts shaped exactly like state.py's real trade/state
schema (see main.py:new_trade_record and state.py:_fresh_state).
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "scripts"))

from app.backend.services import scanner_service, health_service, configuration_service


def _fake_state(**overrides):
    state = {
        "date": "2026-07-26",
        "trades": {
            "NIFTY-CE-1": {
                "trade_id": "NIFTY-CE-1",
                "symbol": "NIFTY",
                "direction": "CE",
                "status": "ACTIVE",
                "alert_time": "2026-07-26T09:45:00+05:30",
            },
            "NIFTY-PE-1": {
                "trade_id": "NIFTY-PE-1",
                "symbol": "NIFTY",
                "direction": "PE",
                "status": "SL_HIT",
                "alert_time": "2026-07-26T10:15:00+05:30",
            },
        },
        "circuit_breaker_tripped": False,
        "consecutive_sl_streak": 0,
        "daily_stats": {
            "scanned_symbols": 24,
            "valid_signals": 2,
            "rejected_signals": 5,
            "api_failures": 0,
            "broker_errors": 0,
        },
        "summary_sent": False,
    }
    state.update(overrides)
    return state


def test_scanner_status_counts_open_trades_correctly(monkeypatch):
    monkeypatch.setattr(
        scanner_service.state_reader, "get_state_for_date", lambda date_str=None: _fake_state()
    )
    status = scanner_service.get_status()
    assert status["total_trades_today"] == 2
    assert status["open_trades"] == 1  # only the ACTIVE one; SL_HIT is terminal
    assert status["circuit_breaker_tripped"] is False


def test_latest_signals_sorted_newest_first(monkeypatch):
    monkeypatch.setattr(
        scanner_service.state_reader, "get_state_for_date", lambda date_str=None: _fake_state()
    )
    signals = scanner_service.get_latest_signals(limit=10)
    assert signals[0]["trade_id"] == "NIFTY-PE-1"  # 10:15 > 09:45
    assert signals[1]["trade_id"] == "NIFTY-CE-1"


def test_signal_history_requires_date():
    try:
        scanner_service.get_signal_history(None)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_health_flags_circuit_breaker(monkeypatch):
    monkeypatch.setattr(
        health_service.state_reader,
        "checkout_status",
        lambda: {"ok": True, "error": None, "last_pull_ist": "2026-07-26T09:00:00+05:30"},
    )
    monkeypatch.setattr(
        health_service.state_reader,
        "get_state_for_date",
        lambda date_str=None: _fake_state(circuit_breaker_tripped=True),
    )
    health = health_service.get_health()
    assert health["status"] != "healthy"
    assert any("circuit breaker" in issue for issue in health["issues"])


def test_health_healthy_when_nothing_is_wrong(monkeypatch):
    monkeypatch.setattr(
        health_service.state_reader,
        "checkout_status",
        lambda: {"ok": True, "error": None, "last_pull_ist": "2026-07-26T09:00:00+05:30"},
    )
    monkeypatch.setattr(
        health_service.state_reader, "get_state_for_date", lambda date_str=None: _fake_state()
    )
    health = health_service.get_health()
    assert health["status"] == "healthy"
    assert health["score"] == 100
    assert health["issues"] == []
    # Part 10: default state (no poll cycle has run yet, e.g. this
    # test never started the app) must not itself count as "frozen"
    # -- never_completed_a_cycle is the honest state, not a failure.
    assert health["live_pipeline"]["watchdog"]["frozen"] is False
    assert health["live_pipeline"]["watchdog"]["never_completed_a_cycle"] is True


def test_health_flags_frozen_live_pipeline(monkeypatch):
    """Part 10: a stalled Phase 3 poll loop must show up in /api/health,
    not just in logs nobody is watching."""
    monkeypatch.setattr(
        health_service.state_reader,
        "checkout_status",
        lambda: {"ok": True, "error": None, "last_pull_ist": "2026-07-26T09:00:00+05:30"},
    )
    monkeypatch.setattr(
        health_service.state_reader, "get_state_for_date", lambda date_str=None: _fake_state()
    )
    from app.backend.services import live_service
    monkeypatch.setattr(
        live_service, "get_watchdog_status",
        lambda: {"frozen": True, "never_completed_a_cycle": False,
                 "seconds_since_last_cycle": 999, "last_cycle_duration_seconds": 0.5,
                 "timeout_count": 2, "stall_threshold_seconds": 60},
    )
    monkeypatch.setattr(
        live_service, "get_failure_status",
        lambda: {"consecutive_failures": {}, "last_errors": {}},
    )

    health = health_service.get_health()
    assert health["status"] != "healthy"
    assert any("frozen" in issue for issue in health["issues"])


def test_health_flags_repeatedly_failing_live_component(monkeypatch):
    monkeypatch.setattr(
        health_service.state_reader,
        "checkout_status",
        lambda: {"ok": True, "error": None, "last_pull_ist": "2026-07-26T09:00:00+05:30"},
    )
    monkeypatch.setattr(
        health_service.state_reader, "get_state_for_date", lambda date_str=None: _fake_state()
    )
    from app.backend.services import live_service
    monkeypatch.setattr(
        live_service, "get_watchdog_status",
        lambda: {"frozen": False, "never_completed_a_cycle": False,
                 "seconds_since_last_cycle": 5, "last_cycle_duration_seconds": 0.2,
                 "timeout_count": 0, "stall_threshold_seconds": 60},
    )
    monkeypatch.setattr(
        live_service, "get_failure_status",
        lambda: {"consecutive_failures": {"replay_progress": 5}, "last_errors": {}},
    )

    health = health_service.get_health()
    assert health["status"] != "healthy"
    assert any("replay_progress" in issue for issue in health["issues"])


def test_configuration_redacts_secret_shaped_names(monkeypatch):
    import types

    fake_config = types.SimpleNamespace(
        MAX_CONCURRENT_TRADES=4,
        SOME_API_KEY="shouldnotappear",
        _private=1,
        lowercase_name="ignored",
    )
    monkeypatch.setattr(configuration_service, "config_mod", fake_config)
    snapshot = configuration_service.get_configuration()
    assert snapshot["MAX_CONCURRENT_TRADES"] == 4
    assert snapshot["SOME_API_KEY"] == "***REDACTED***"
    assert "_private" not in snapshot
    assert "lowercase_name" not in snapshot
