"""
lifecycle_analytics.py
------------------------
Analytics aggregator for the Trade Lifecycle / BTST-STBT dashboard
tabs (Part 12). Computes ONLY the fields the spec asks for:

    Total Alerts, Closed Today, Open At EOD, BTST Recommended,
    STBT Recommended, BTST Success Rate, STBT Success Rate,
    Average Holding Time, Overnight Return

REUSE, NOT DUPLICATION: every win-rate/loss-rate number underneath
"BTST Success Rate" / "STBT Success Rate" is computed by
app.engines.replay_analytics.performance_analytics.compute_performance()
-- the exact same pure function analytics_service.py already reuses
for the intraday Trade Analytics endpoints (see that module's own
docstring: "Reuses performance_analytics.compute_performance() ...
as-is"). No new analytics ENGINE is created here; this module is pure
mapping/aggregation glue between two existing data sources (today's
intraday `state` dict, btst_state's per-entry-date position dicts) and
that one existing pure function.

This module takes plain dicts/lists as input (no I/O of its own) so it
can be called identically from:
  - a CLI/report script running inside the GitHub Actions job (which
    already has `state` and can load btst_state files directly), and
  - app/backend/services/trade_lifecycle_service.py (which fetches the
    same shapes via state_reader / btst_state for the dashboard API).
"""

from datetime import datetime, timezone, timedelta

from app.engines.replay_analytics import performance_analytics
import trade_lifecycle

IST = timezone(timedelta(hours=5, minutes=30))

_CLOSED_LIFECYCLE_STATUSES = {
    trade_lifecycle.STATUS_TARGET_HIT,
    trade_lifecycle.STATUS_SL_HIT,
    trade_lifecycle.STATUS_CLOSED,
}


def _parse_iso(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None


def _btst_pnl_pct(position: dict):
    entry = position.get("tracked_entry_price")
    exit_price = position.get("exit_price")
    if not entry or exit_price is None:
        return None
    return round((exit_price - entry) / entry * 100, 2)


def _btst_holding_minutes(position: dict):
    """Overnight hold: from the BTST entry evening (~15:25 IST on
    btst_entry_date, when the position was opened at the prior day's
    EOD quote -- see btst_main.new_position_record()'s docstring) to
    whichever exit timestamp field is present."""
    entry_date = position.get("btst_entry_date")
    if not entry_date:
        return None
    entry_dt = _parse_iso(f"{entry_date}T15:25:00+05:30")
    exit_dt = _parse_iso(
        position.get("t2_hit_time") or position.get("sl_hit_time")
    )
    if entry_dt is None or exit_dt is None:
        return None
    return round((exit_dt - entry_dt).total_seconds() / 60, 1)


def _btst_outcome(position: dict):
    """
    Maps btst_tracker.py's internal final_status to a WIN/LOSS/
    BREAKEVEN outcome label (performance_analytics.compute_performance()'s
    expected input). AMBIGUOUS and any non-terminal position are
    excluded entirely (return None) -- same convention
    performance_analytics already applies to NO_ENTRY/INVALID_SIGNAL,
    just filtered out one level up here instead of inside that
    function, so its own code needs zero changes.
    """
    fs = position.get("final_status")
    if fs is None or fs == "AMBIGUOUS":
        return None
    if fs == "T2_HIT":
        return "WIN"
    if fs in ("SL_HIT", "GAP_SL_HIT", "T1_THEN_SL"):
        return "LOSS"
    # FORCE_EXIT / T1_THEN_FORCE_EXIT: exited at the exit day's last
    # candle close (btst_tracker.py), not at a defined SL/target -- the
    # realized PnL sign is the only honest way to classify these.
    pnl = _btst_pnl_pct(position)
    if pnl is None:
        return None
    if pnl > 0.05:
        return "WIN"
    if pnl < -0.05:
        return "LOSS"
    return "BREAKEVEN"


def _btst_record(position: dict) -> dict:
    """Maps one BTST/STBT position dict into
    performance_analytics.compute_performance()'s expected record
    shape. Only called for positions _btst_outcome() didn't exclude."""
    return {
        "outcome": _btst_outcome(position),
        "pnl_pct": _btst_pnl_pct(position) or 0.0,
        "max_drawdown_pct": abs(position.get("max_adverse_pct") or 0.0),
        "holding_minutes": _btst_holding_minutes(position),
    }


def _is_stbt(position: dict) -> bool:
    return position.get("strategy") == "stbt"


def compute_btst_stbt_success(all_btst_positions: list) -> dict:
    """
    all_btst_positions: every BTST/STBT position dict across whatever
    lookback window the caller chooses (one flat list, already merged
    across btst_state's per-entry-date files).
    """
    btst_positions = [p for p in all_btst_positions if not _is_stbt(p)]
    stbt_positions = [p for p in all_btst_positions if _is_stbt(p)]

    btst_records = [_btst_record(p) for p in btst_positions if _btst_outcome(p) is not None]
    stbt_records = [_btst_record(p) for p in stbt_positions if _btst_outcome(p) is not None]

    btst_perf = performance_analytics.compute_performance(btst_records)
    stbt_perf = performance_analytics.compute_performance(stbt_records)

    return {
        "btst_success_rate_pct": btst_perf["win_rate"],
        "stbt_success_rate_pct": stbt_perf["win_rate"],
        "btst_performance": btst_perf,
        "stbt_performance": stbt_perf,
    }


def compute_overnight_return(all_btst_positions: list):
    """Average realized overnight % move across every CLOSED BTST +
    STBT position (win, loss, or breakeven alike) -- the actual
    overnight return the carry produced, as opposed to the success
    rate (which only counts win/loss)."""
    records = [_btst_record(p) for p in all_btst_positions if _btst_outcome(p) is not None]
    if not records:
        return None
    return round(sum(r["pnl_pct"] for r in records) / len(records), 2)


def compute_lifecycle_counts(today_state: dict) -> dict:
    """
    Counts scoped to TODAY's intraday `state` dict (state.py's
    state["trades"]) only -- Total Alerts / Closed Today / Open At EOD
    / BTST Recommended / STBT Recommended. Reads trade_lifecycle.
    lifecycle_status() and trade["btst_recommendation"] (Part 12's
    additive field, see btst_recommendation.py) -- never recomputes
    either.

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

    btst_recommended = 0
    stbt_recommended = 0
    for t in trades:
        rec = t.get("btst_recommendation")
        if not rec or rec.get("recommendation") != "YES":
            continue
        if rec.get("carry_type") == "BTST":
            btst_recommended += 1
        elif rec.get("carry_type") == "STBT":
            stbt_recommended += 1

    holding_times = [
        t["holding_time_minutes"] for t in trades
        if t.get("holding_time_minutes") is not None
    ]
    avg_holding = round(sum(holding_times) / len(holding_times), 1) if holding_times else None

    return {
        "total_alerts": total_alerts,
        "closed_today": closed_today,
        "open_at_eod": open_at_eod,
        "btst_recommended": btst_recommended,
        "stbt_recommended": stbt_recommended,
        "average_holding_time_minutes": avg_holding,
    }


def get_lifecycle_analytics(today_state: dict, all_btst_positions: list) -> dict:
    """The one function a caller (CLI report or the dashboard service)
    needs. Pure function, no I/O -- caller supplies both inputs."""
    result = {}
    result.update(compute_lifecycle_counts(today_state))
    result.update(compute_btst_stbt_success(all_btst_positions))
    result["overnight_return_pct"] = compute_overnight_return(all_btst_positions)
    return result
