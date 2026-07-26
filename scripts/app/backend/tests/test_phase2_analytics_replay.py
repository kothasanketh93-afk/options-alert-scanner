"""
Unit tests for Phase 2 (AnalyticsService, ReplayService). Monkeypatches
trade_journal.read_journal/read_journal_range and daily_cache.read --
never touches git or the real state branch. Entry fixtures are shaped
exactly like trade_journal.build_journal_entry()'s real output.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "scripts"))

import daily_cache
from app.engines.replay_analytics import trade_journal
from app.backend.services import replay_service, analytics_service


FAKE_ENTRIES = [
    {"trade_id": "T1", "symbol": "NIFTY", "outcome": "WIN", "pnl_pct": 12.5,
     "confidence": 82, "probability": 70, "trade_quality": 4, "holding_minutes": 35,
     "max_drawdown_pct": 2.1, "decision": {"decision": "Trade Approved"}},
    {"trade_id": "T2", "symbol": "NIFTY", "outcome": "LOSS", "pnl_pct": -8.0,
     "confidence": 55, "probability": 45, "trade_quality": 2, "holding_minutes": 20,
     "max_drawdown_pct": 9.0, "decision": {"decision": "Trade Approved"}},
    {"trade_id": "T3", "symbol": "BANKNIFTY", "outcome": "NO_ENTRY", "pnl_pct": 0.0},
]


def _patch_reads(monkeypatch):
    monkeypatch.setattr(replay_service.state_reader, "ensure_state_available", lambda: True)
    monkeypatch.setattr(analytics_service.state_reader, "ensure_state_available", lambda: True)
    monkeypatch.setattr(trade_journal, "read_journal", lambda date_str: FAKE_ENTRIES)
    monkeypatch.setattr(trade_journal, "read_journal_range", lambda s, e: FAKE_ENTRIES)


def test_get_journal_returns_entries(monkeypatch):
    _patch_reads(monkeypatch)
    result = replay_service.get_journal("2026-07-25")
    assert result["count"] == 3
    assert result["enabled"] is True


def test_daily_summary_reports_not_available_when_uncomputed(monkeypatch):
    _patch_reads(monkeypatch)
    monkeypatch.setattr(daily_cache, "read", lambda subdir, date_str, compressed=False: None)
    result = replay_service.get_daily_summary("2026-07-25")
    assert result["available"] is False


def test_daily_summary_returns_cached_report_when_present(monkeypatch):
    _patch_reads(monkeypatch)
    monkeypatch.setattr(
        daily_cache, "read",
        lambda subdir, date_str, compressed=False: {"date": date_str, "win_rate": 55.0},
    )
    result = replay_service.get_daily_summary("2026-07-25")
    assert result["available"] is True
    assert result["summary"]["win_rate"] == 55.0


def test_performance_excludes_no_entry_trades(monkeypatch):
    _patch_reads(monkeypatch)
    result = analytics_service.get_performance("2026-07-20", "2026-07-25")
    perf = result["performance"]
    assert perf["total_trades"] == 2  # NO_ENTRY excluded
    assert perf["wins"] == 1
    assert perf["losses"] == 1


def test_trade_statistics_averages_confidence_and_probability(monkeypatch):
    _patch_reads(monkeypatch)
    result = analytics_service.get_trade_statistics("2026-07-20", "2026-07-25")
    assert result["avg_confidence"] == round((82 + 55) / 2, 1)
    assert result["avg_probability"] == round((70 + 45) / 2, 1)


def test_scanner_health_reports_false_negative_limitation(monkeypatch):
    _patch_reads(monkeypatch)
    result = analytics_service.get_scanner_health("2026-07-20", "2026-07-25")
    assert result["health"]["false_negative_rate"] == 0.0
    assert "limitation" in result


def test_analytics_service_reports_disabled_when_replay_analytics_off(monkeypatch):
    monkeypatch.setattr(analytics_service, "REPLAY_ANALYTICS_ENABLED", False)
    result = analytics_service.get_performance("2026-07-20", "2026-07-25")
    assert result["enabled"] is False
