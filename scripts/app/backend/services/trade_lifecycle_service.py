"""
trade_lifecycle_service.py
----------------------------
Part 12 -- backend service for the Trade Lifecycle dashboard tabs
(Open Trades / Closed Today / Open At EOD / BTST / STBT / Carry
History) and the Analytics panel underneath them.

Read-only, same contract as every other service in this package (see
state_reader.py's own docstring): never calls state.save_state() or
btst_state.save(), never imports a broker module. Reuses trade_
lifecycle.py's to_dashboard_record()/lifecycle_status() and lifecycle_
analytics.py's get_lifecycle_analytics() as-is -- no display/label or
analytics logic is recomputed here, this module only fetches the raw
dicts those two already know how to turn into records/stats.
"""

from datetime import timedelta

import btst_state
import trade_lifecycle
import lifecycle_analytics
from app.backend.services import state_reader

BTST_LOOKBACK_DAYS_DEFAULT = 10


def _btst_subdir_dates(lookback_days: int) -> list:
    """Entry dates (most recent first) that have a btst/<date>.json
    file on the state branch, within the lookback window -- same glob
    approach as state_reader.list_available_dates(), just pointed at
    the btst/ subdir instead of state/."""
    import state as state_mod

    state_dir = state_mod.STATE_CLONE_DIR / btst_state.SUBDIR
    if not state_dir.exists():
        return []

    from datetime import datetime
    cutoff = state_mod.ist_now() - timedelta(days=lookback_days)
    dates = []
    for path in state_dir.glob("*.json"):
        stem = path.stem
        try:
            file_date = datetime.strptime(stem, "%Y-%m-%d").replace(tzinfo=cutoff.tzinfo)
        except ValueError:
            continue
        if file_date >= cutoff:
            dates.append(stem)
    return sorted(dates, reverse=True)


def _load_btst_positions(lookback_days: int = BTST_LOOKBACK_DAYS_DEFAULT) -> list:
    state_reader.ensure_state_available()
    positions = []
    for entry_date in _btst_subdir_dates(lookback_days):
        payload = btst_state.load(entry_date)
        positions.extend(payload.get("positions", {}).values())
    return positions


def _today_trades() -> list:
    state = state_reader.get_state_for_date()
    return list(state.get("trades", {}).values())


def get_open_trades() -> list:
    """Trades still live and NOT yet flagged OPEN AT EOD."""
    trades = _today_trades()
    return [
        trade_lifecycle.to_dashboard_record(t)
        for t in trades
        if trade_lifecycle.lifecycle_status(t) == trade_lifecycle.STATUS_OPEN
    ]


def get_closed_today() -> list:
    """Trades resolved today: TARGET HIT / SL HIT / CLOSED."""
    trades = _today_trades()
    closed_statuses = {
        trade_lifecycle.STATUS_TARGET_HIT,
        trade_lifecycle.STATUS_SL_HIT,
        trade_lifecycle.STATUS_CLOSED,
    }
    return [
        trade_lifecycle.to_dashboard_record(t)
        for t in trades
        if trade_lifecycle.lifecycle_status(t) in closed_statuses
    ]


def get_open_at_eod() -> list:
    """Trades CURRENTLY in OPEN AT EOD status (not merely ever flagged
    with `_marked_open_at_eod` -- that flag is never cleared, so a
    trade marked at ~15:25 IST that then genuinely hits target/SL
    before the real 15:30 close must NOT still show up here; only
    trade_lifecycle.lifecycle_status() correctly reflects that), with
    their BTST/STBT recommendation attached if the engine has run for
    them (Part 12's trade["btst_recommendation"] field, additive --
    see btst_recommendation.py)."""
    trades = _today_trades()
    records = []
    for t in trades:
        if trade_lifecycle.lifecycle_status(t) != trade_lifecycle.STATUS_OPEN_AT_EOD:
            continue
        record = trade_lifecycle.to_dashboard_record(t)
        record["btst_recommendation"] = t.get("btst_recommendation")
        records.append(record)
    return records


def get_btst_positions(lookback_days: int = BTST_LOOKBACK_DAYS_DEFAULT) -> list:
    """Currently-OPEN BTST positions (strategy != 'stbt')."""
    positions = _load_btst_positions(lookback_days)
    return [
        trade_lifecycle.to_dashboard_record(p, is_btst=True)
        for p in positions
        if p.get("strategy") != "stbt" and p.get("status") == "OPEN"
    ]


def get_stbt_positions(lookback_days: int = BTST_LOOKBACK_DAYS_DEFAULT) -> list:
    """Currently-OPEN STBT positions (strategy == 'stbt') -- see
    trade_lifecycle.py's own docstring: STBT vocabulary support exists,
    but no STBT-specific scanning engine has ever populated
    strategy == 'stbt' upstream, so this is empty until/unless one
    does. Included for spec completeness and forward-compatibility."""
    positions = _load_btst_positions(lookback_days)
    return [
        trade_lifecycle.to_dashboard_record(p, is_btst=True)
        for p in positions
        if p.get("strategy") == "stbt" and p.get("status") == "OPEN"
    ]


def get_carry_history(lookback_days: int = BTST_LOOKBACK_DAYS_DEFAULT) -> list:
    """Every RESOLVED (terminal) BTST/STBT position within the lookback
    window -- the actual overnight-carry outcomes, for the Carry
    History tab."""
    positions = _load_btst_positions(lookback_days)
    terminal = {"T2_HIT", "SL_HIT", "GAP_SL_HIT", "FORCE_EXIT", "AMBIGUOUS"}
    return [
        trade_lifecycle.to_dashboard_record(p, is_btst=True)
        for p in positions
        if p.get("status") in terminal
    ]


def get_analytics(lookback_days: int = BTST_LOOKBACK_DAYS_DEFAULT) -> dict:
    """Total Alerts / Closed Today / Open At EOD / BTST Recommended /
    STBT Recommended / BTST Success Rate / STBT Success Rate / Average
    Holding Time / Overnight Return -- see lifecycle_analytics.py,
    which itself reuses replay_analytics.performance_analytics as-is."""
    state = state_reader.get_state_for_date()
    positions = _load_btst_positions(lookback_days)
    return lifecycle_analytics.get_lifecycle_analytics(state, positions)
