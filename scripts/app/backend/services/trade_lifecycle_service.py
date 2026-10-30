"""
trade_lifecycle_service.py
----------------------------
Part 12 -- backend service for the Trade Lifecycle dashboard tabs
(Open Trades / Closed Today / Open At EOD) and the Analytics panel
underneath them.

(The BTST / STBT / Carry History tabs this Part originally shipped
alongside -- backed by the standalone btst_*.py module's position
files -- were removed entirely on 2026-10-30 at the user's explicit
request. See trade_lifecycle.py/lifecycle_analytics.py's own updated
docstrings for the matching removal on that side.)

Read-only, same contract as every other service in this package (see
state_reader.py's own docstring): never calls state.save_state(),
never imports a broker module. Reuses trade_lifecycle.py's
to_dashboard_record()/lifecycle_status() and lifecycle_analytics.py's
get_lifecycle_analytics() as-is -- no display/label or analytics
logic is recomputed here, this module only fetches the raw dicts
those two already know how to turn into records/stats.
"""

import trade_lifecycle
import lifecycle_analytics
from app.backend.services import state_reader


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
    trade_lifecycle.lifecycle_status() correctly reflects that)."""
    trades = _today_trades()
    return [
        trade_lifecycle.to_dashboard_record(t)
        for t in trades
        if trade_lifecycle.lifecycle_status(t) == trade_lifecycle.STATUS_OPEN_AT_EOD
    ]


def get_analytics() -> dict:
    """Total Alerts / Closed Today / Open At EOD / Average Holding
    Time -- see lifecycle_analytics.py."""
    state = state_reader.get_state_for_date()
    return lifecycle_analytics.get_lifecycle_analytics(state)
