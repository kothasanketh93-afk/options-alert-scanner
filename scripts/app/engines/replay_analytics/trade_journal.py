"""
app/engines/replay_analytics/trade_journal.py -- Trade Journal.
------------------------------------------------------------------------------
Maintains a structured journal of every completed (closed) trade:
Timestamp, Symbol, Direction, Entry, Stop Loss, Targets, Outcome,
Confidence, Probability, Trade Quality, Reason, Comments.

Persistence reuses daily_cache.py's EXISTING state-branch JSON
mechanism (same one opportunity_score.py/instrument_master.py already
use) rather than inventing a new storage path -- one journal file per
day under config.REPLAY_JOURNAL_SUBDIR, siblings of state.py's own
state/<date>.json.

This module does NOT re-decide outcomes -- entries are built from
already-classified records (outcome_classifier.classify_outcome() +
signal_replay.replay_signal() results), it only normalizes/persists them.
"""

from datetime import datetime, timezone, timedelta

import daily_cache
from config import REPLAY_JOURNAL_SUBDIR
from logging_setup import get_logger

IST = timezone(timedelta(hours=5, minutes=30))
_logger = get_logger("replay_analytics")

_JOURNAL_CACHE = daily_cache.DailyCache(REPLAY_JOURNAL_SUBDIR)


def build_journal_entry(trade: dict, replay_result: dict, outcome: str) -> dict:
    """One structured entry per Part 7's Trade Journal spec."""
    return {
        "timestamp": trade.get("signal_time") or trade.get("alert_time"),
        "trade_id": trade.get("trade_id"),
        "symbol": trade.get("symbol"),
        "direction": trade.get("direction"),
        "entry": replay_result.get("entry"),
        "stop_loss": replay_result.get("stop_loss"),
        "targets": {
            "target_1": replay_result.get("target_1"),
            "target_2": replay_result.get("target_2"),
            "target_3": replay_result.get("target_3"),
        },
        "outcome": outcome,
        "confidence": trade.get("confidence_score"),
        "probability": trade.get("probability_pct"),
        "trade_quality": trade.get("trade_quality"),
        "reason": replay_result.get("exit_reason"),
        "comments": trade.get("comments", ""),
        # extra fields performance_analytics/learning_engine expect --
        # additive, doesn't break anyone reading only the fields above
        "pnl_pct": _pnl_pct(replay_result),
        "max_drawdown_pct": replay_result.get("max_drawdown_pct", 0.0),
        "holding_minutes": replay_result.get("time_to_target") or replay_result.get("time_to_stop_loss"),
        "market_regime": trade.get("market_regime"),
        "timeframe": trade.get("timeframe"),
        "entry_type": trade.get("entry_type") or trade.get("strategy"),
        "pattern": trade.get("pattern"),
        "moneyness": trade.get("moneyness"),
        "score": trade.get("trade_score"),
        "options_intelligence": trade.get("options_intelligence"),
        "decision": trade.get("decision"),
    }


def _pnl_pct(replay_result: dict) -> float:
    entry = replay_result.get("entry")
    exit_price = replay_result.get("exit_price")
    if not entry or exit_price is None:
        return 0.0
    return round((exit_price - entry) / entry * 100, 2)


def append_entries(date_str: str, entries: list) -> bool:
    """Merges `entries` into that day's journal file (by trade_id, so
    re-running analytics for a date that already has a journal doesn't
    duplicate rows) and writes it back."""
    existing = _JOURNAL_CACHE.read(date_str) or {"date": date_str, "entries": []}
    by_id = {e.get("trade_id"): e for e in existing["entries"] if e.get("trade_id")}
    for e in entries:
        if e.get("trade_id"):
            by_id[e["trade_id"]] = e
        else:
            existing["entries"].append(e)
    existing["entries"] = list(by_id.values()) + [e for e in existing["entries"] if not e.get("trade_id")]
    ok = _JOURNAL_CACHE.write(date_str, existing,
                              commit_message=f"replay_analytics: journal update {date_str}")
    if not ok:
        _logger.error("trade_journal: failed to persist journal for %s", date_str)
    return ok


def read_journal(date_str: str) -> list:
    payload = _JOURNAL_CACHE.read(date_str)
    return payload["entries"] if payload else []


def read_journal_range(start_date_str: str, end_date_str: str) -> list:
    """Inclusive date range, both 'YYYY-MM-DD'. Reads day-by-day (each
    a small, independent file) -- no separate index needed since a
    replay/analytics run over N days is already O(N) daily_cache reads
    either way."""
    start = datetime.strptime(start_date_str, "%Y-%m-%d")
    end = datetime.strptime(end_date_str, "%Y-%m-%d")
    entries = []
    day = start
    while day <= end:
        entries.extend(read_journal(day.strftime("%Y-%m-%d")))
        day += timedelta(days=1)
    return entries
