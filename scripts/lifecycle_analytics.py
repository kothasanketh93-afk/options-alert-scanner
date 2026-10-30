"""
lifecycle_analytics.py
------------------------
Analytics aggregator for the Trade Lifecycle dashboard tabs (Part 12).
Computes ONLY the fields the spec asks for:

    Total Alerts, Closed Today, Open At EOD, Average Holding Time

(The BTST/STBT Recommendation Engine this Part originally shipped
alongside -- BTST Recommended/STBT Recommended counts, BTST/STBT
Success Rate, Overnight Return, all of it driven by the standalone
btst_*.py module's position files -- was removed entirely on
2026-10-30 at the user's explicit request. See trade_lifecycle.py's
own docstring for the matching removal on that side.)

This module takes a plain dict as input (no I/O of its own) so it can
be called identically from a CLI/report script running inside the
GitHub Actions job, or from
app/backend/services/trade_lifecycle_service.py for the dashboard API.
"""

from datetime import datetime, timezone, timedelta

import trade_lifecycle

IST = timezone(timedelta(hours=5, minutes=30))

_CLOSED_LIFECYCLE_STATUSES = {
    trade_lifecycle.STATUS_TARGET_HIT,
    trade_lifecycle.STATUS_SL_HIT,
    trade_lifecycle.STATUS_CLOSED,
}


def compute_lifecycle_counts(today_state: dict) -> dict:
    """
    Counts scoped to TODAY's intraday `state` dict (state.py's
    state["trades"]) only -- Total Alerts / Closed Today / Open At EOD.
    Reads trade_lifecycle.lifecycle_status() -- never recomputes it.

    IMPORTANT: both counts below classify by `lifecycle_status(t)`,
    NOT by the raw `_marked_open_at_eod` flag directly. A trade can be
    marked OPEN AT EOD at ~15:25 IST and then still resolve to a real
    TARGET HIT / SL HIT before the actual 15:30 close -- once that
    happens, trade_lifecycle.lifecycle_status() correctly reports the
    terminal status instead of OPEN AT EOD (see that function's own
    branching), but the `_marked_open_at_eod` flag itself is never
    cleared. Counting on the raw flag would double-count that trade as
    still "Open At EOD" and simultaneously drop it from "Closed Today".
    """
    trades = list((today_state or {}).get("trades", {}).values())
    total_alerts = len(trades)

    statuses = [trade_lifecycle.lifecycle_status(t) for t in trades]
    closed_today = sum(1 for s in statuses if s in _CLOSED_LIFECYCLE_STATUSES)
    open_at_eod = sum(1 for s in statuses if s == trade_lifecycle.STATUS_OPEN_AT_EOD)

    holding_times = [
        t["holding_time_minutes"] for t in trades
        if t.get("holding_time_minutes") is not None
    ]
    avg_holding = round(sum(holding_times) / len(holding_times), 1) if holding_times else None

    return {
        "total_alerts": total_alerts,
        "closed_today": closed_today,
        "open_at_eod": open_at_eod,
        "average_holding_time_minutes": avg_holding,
    }


def get_lifecycle_analytics(today_state: dict) -> dict:
    """The one function a caller (CLI report or the dashboard service)
    needs. Pure function, no I/O -- caller supplies the input."""
    return compute_lifecycle_counts(today_state)
