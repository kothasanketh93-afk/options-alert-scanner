"""
scanner_service.py
-------------------
Read-only view of today's (or a past day's) scanner state, built
entirely from state_reader.get_state_for_date(). Adds no new
business logic -- every number here is either copied straight out of
the state file or a simple count/filter over trades already in it.
"""

from app.backend.services import state_reader

OPEN_STATUSES = ("WAITING_ENTRY", "ACTIVE", "T1_HIT")


def _trade_list(state: dict) -> list:
    return list(state.get("trades", {}).values())


def get_status(date_str: str = None) -> dict:
    state = state_reader.get_state_for_date(date_str)
    trades = _trade_list(state)
    open_trades = [t for t in trades if t.get("status") in OPEN_STATUSES]

    return {
        "date": state.get("date"),
        "total_trades_today": len(trades),
        "open_trades": len(open_trades),
        "circuit_breaker_tripped": state.get("circuit_breaker_tripped", False),
        "consecutive_sl_streak": state.get("consecutive_sl_streak", 0),
        "daily_stats": state.get("daily_stats", {}),
        "summary_sent": state.get("summary_sent", False),
        "recovered_from_corruption": state.get("_recovered_from_corruption", False),
    }


def get_open_trades(date_str: str = None) -> list:
    state = state_reader.get_state_for_date(date_str)
    return [t for t in _trade_list(state) if t.get("status") in OPEN_STATUSES]


def get_latest_signals(date_str: str = None, limit: int = 20) -> list:
    """
    Every trade from the given day's state (default: today), newest
    alert first. This *is* "latest signals" -- there's no separate
    signals feed distinct from state["trades"] in the existing app,
    so this deliberately doesn't invent one.
    """
    state = state_reader.get_state_for_date(date_str)
    trades = _trade_list(state)
    trades.sort(key=lambda t: t.get("alert_time") or "", reverse=True)
    return trades[:limit]


def get_signal_history(date_str: str) -> list:
    """Full trade list for one specific past day."""
    if not date_str:
        raise ValueError("date_str is required for history lookups")
    state = state_reader.get_state_for_date(date_str)
    return _trade_list(state)


def get_available_history_dates(lookback_days: int = None) -> list:
    return state_reader.list_available_dates(lookback_days)


def get_trades_by_id(date_str: str = None) -> dict:
    """
    Same trades as get_latest_signals()/get_signal_history(), keyed by
    trade_id (state.py's own dict shape) instead of flattened to a
    list. Added for Phase 3 (live_service's poll-and-diff loop needs
    to detect new trade_ids and per-trade status changes across poll
    cycles) -- no new business logic, just exposes the dict state
    already stores instead of only its .values().
    """
    state = state_reader.get_state_for_date(date_str)
    return dict(state.get("trades", {}))
