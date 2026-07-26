"""
Unit + integration tests for Phase 3 (poll-and-diff WebSocket
streaming). Same conventions as the Phase 1/2 test files: monkeypatch
the service layer with fixture data shaped like the real schemas,
never touch git or the real state branch.

test_live_service_* -- pure unit tests of the diff logic in
live_service.py, no FastAPI/websocket involved.

test_ws_dashboard_* -- integration tests using Starlette's TestClient
websocket support, exercising the actual /ws/dashboard route and the
real ConnectionManager, with live_service's underlying services
monkeypatched for determinism.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "scripts"))

from app.backend.services import live_service, scanner_service, health_service, replay_service


def _fake_state(**overrides):
    state = {
        "date": "2026-07-26",
        "trades": {
            "NIFTY-CE-1": {
                "trade_id": "NIFTY-CE-1", "symbol": "NIFTY", "direction": "CE",
                "status": "ACTIVE", "alert_time": "2026-07-26T09:45:00+05:30",
            },
        },
        "circuit_breaker_tripped": False,
        "consecutive_sl_streak": 0,
        "daily_stats": {"scanned_symbols": 24, "valid_signals": 1, "rejected_signals": 5,
                         "api_failures": 0, "broker_errors": 0},
        "summary_sent": False,
    }
    state.update(overrides)
    return state


def _patch_common(monkeypatch, state):
    monkeypatch.setattr(scanner_service.state_reader, "get_state_for_date", lambda date_str=None: state)
    monkeypatch.setattr(
        health_service.state_reader, "checkout_status",
        lambda: {"ok": True, "error": None, "last_pull_ist": "2026-07-26T09:00:00+05:30"},
    )
    monkeypatch.setattr(health_service.state_reader, "get_state_for_date", lambda date_str=None: state)
    monkeypatch.setattr(replay_service, "REPLAY_ANALYTICS_ENABLED", False)
    monkeypatch.setattr(live_service, "_today", lambda: state["date"])


def _reset_last_snapshot():
    live_service._last = live_service._LastSnapshot()


def test_build_full_snapshot_emits_everything_once(monkeypatch):
    _reset_last_snapshot()
    _patch_common(monkeypatch, _fake_state())
    events = live_service.build_full_snapshot()
    types = {e["type"] for e in events}
    assert "scanner_status" in types
    assert "system_health" in types
    assert "new_signals" in types
    assert "replay_progress" not in types  # REPLAY_ANALYTICS_ENABLED is False


def test_full_snapshot_does_not_touch_shared_cursor(monkeypatch):
    _reset_last_snapshot()
    _patch_common(monkeypatch, _fake_state())
    live_service.build_full_snapshot()
    assert live_service._last.scanner_status is None
    assert live_service._last.trades_by_id == {}


def test_compute_diff_events_empty_when_nothing_changed(monkeypatch):
    _reset_last_snapshot()
    state = _fake_state()
    _patch_common(monkeypatch, state)
    first = live_service.compute_diff_events()
    assert first  # first cycle always reports everything as new
    second = live_service.compute_diff_events()
    assert second == []  # nothing changed between cycles


def test_compute_diff_events_reports_new_signal(monkeypatch):
    _reset_last_snapshot()
    state = _fake_state()
    _patch_common(monkeypatch, state)
    live_service.compute_diff_events()  # baseline cycle

    state["trades"]["NIFTY-PE-1"] = {
        "trade_id": "NIFTY-PE-1", "symbol": "NIFTY", "direction": "PE",
        "status": "WAITING_ENTRY", "alert_time": "2026-07-26T10:15:00+05:30",
    }
    events = live_service.compute_diff_events()
    new_signal_events = [e for e in events if e["type"] == "new_signals"]
    assert len(new_signal_events) == 1
    assert new_signal_events[0]["data"][0]["trade_id"] == "NIFTY-PE-1"


def test_compute_diff_events_reports_trade_status_change(monkeypatch):
    _reset_last_snapshot()
    state = _fake_state()
    _patch_common(monkeypatch, state)
    live_service.compute_diff_events()  # baseline cycle

    state["trades"]["NIFTY-CE-1"]["status"] = "T1_HIT"
    events = live_service.compute_diff_events()
    update_events = [e for e in events if e["type"] == "trade_update"]
    assert len(update_events) == 1
    assert update_events[0]["data"][0]["status"] == "T1_HIT"


def test_compute_diff_events_reports_health_change(monkeypatch):
    _reset_last_snapshot()
    state = _fake_state()
    _patch_common(monkeypatch, state)
    live_service.compute_diff_events()  # baseline cycle

    state["circuit_breaker_tripped"] = True
    events = live_service.compute_diff_events()
    health_events = [e for e in events if e["type"] == "system_health"]
    assert len(health_events) == 1
    assert health_events[0]["data"]["status"] != "healthy"


def test_one_source_failure_does_not_block_the_others(monkeypatch):
    """Part 10: if replay_service raises, scanner_status/system_health/
    trades must still be evaluated and broadcast for this cycle."""
    _reset_last_snapshot()
    state = _fake_state()
    _patch_common(monkeypatch, state)
    monkeypatch.setattr(replay_service, "REPLAY_ANALYTICS_ENABLED", True)

    def _boom(date_str=None):
        raise RuntimeError("simulated replay_service failure")

    monkeypatch.setattr(replay_service, "get_journal", _boom)

    events = live_service.compute_diff_events()
    types = {e["type"] for e in events}
    assert "scanner_status" in types
    assert "system_health" in types
    assert "new_signals" in types
    assert "replay_progress" not in types  # this source failed, so it's skipped, not fabricated

    status = live_service.get_failure_status()
    assert status["consecutive_failures"]["replay_progress"] == 1
    assert status["consecutive_failures"].get("scanner_status", 0) == 0


def test_failed_source_recovers_and_diffs_correctly_next_cycle(monkeypatch):
    """A source that fails once, then succeeds, must not falsely report
    'nothing changed' -- it should compare against the last real value."""
    _reset_last_snapshot()
    state = _fake_state()
    _patch_common(monkeypatch, state)

    call_count = {"n": 0}
    real_get_status = scanner_service.get_status

    def _flaky_get_status(date_str=None):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise RuntimeError("simulated transient failure")
        return real_get_status(date_str)

    monkeypatch.setattr(scanner_service, "get_status", _flaky_get_status)

    first = live_service.compute_diff_events()  # cycle 1: succeeds, baseline set
    assert any(e["type"] == "scanner_status" for e in first)

    second = live_service.compute_diff_events()  # cycle 2: raises, isolated
    assert not any(e["type"] == "scanner_status" for e in second)
    assert live_service.get_failure_status()["consecutive_failures"]["scanner_status"] == 1

    third = live_service.compute_diff_events()  # cycle 3: recovers
    assert live_service.get_failure_status()["consecutive_failures"]["scanner_status"] == 0
    # status content unchanged since cycle 1, so no scanner_status event -- proves
    # the failed cycle 2 did not corrupt the diff baseline
    assert not any(e["type"] == "scanner_status" for e in third)


def test_analytics_progress_never_emitted(monkeypatch):
    """Phase 3 deliberately does not fabricate an 'analytics_progress'
    event -- see live_service.py's module docstring."""
    _reset_last_snapshot()
    _patch_common(monkeypatch, _fake_state())
    all_types = set()
    all_types |= {e["type"] for e in live_service.build_full_snapshot()}
    all_types |= {e["type"] for e in live_service.compute_diff_events()}
    assert "analytics_progress" not in all_types


# ---------------------------------------------------------------------
# Integration: the actual /ws/dashboard route + ConnectionManager
# ---------------------------------------------------------------------

def test_ws_dashboard_sends_initial_snapshot(monkeypatch):
    from fastapi.testclient import TestClient
    from app.backend.api.app import create_app

    _reset_last_snapshot()
    _patch_common(monkeypatch, _fake_state())

    app = create_app()
    with TestClient(app) as client:
        with client.websocket_connect("/ws/dashboard") as ws:
            received_types = set()
            # build_full_snapshot() emits scanner_status, system_health,
            # and new_signals for this fixture (replay disabled) -- read
            # exactly that many messages.
            for _ in range(3):
                msg = ws.receive_json()
                received_types.add(msg["type"])
            assert received_types == {"scanner_status", "system_health", "new_signals"}
