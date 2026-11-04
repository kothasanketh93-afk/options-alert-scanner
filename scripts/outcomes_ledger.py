"""
Outcomes ledger (2026-10-31): one CSV row per RESOLVED shadow trade, appended to
ledger/outcomes.csv on the state branch so results survive the one-day state
retention. Rows are never rewritten; a trade_id already present is skipped.

result_pct is the approximate premium return of the exit actually recorded:
  SL_HIT -> stop_loss, T2_HIT -> target_2, EOD_CLOSED -> eod_last_price (entry-relative).
It is BEFORE brokerage/STT/slippage. NOT_ENTERED/AMBIGUOUS rows have no result_pct.
Trailing-stop moves update stop_loss in place, so SL_HIT after a trail is approximate.
"""
import csv
import io
from pathlib import Path

import config

TERMINAL = {"T2_HIT", "SL_HIT", "NOT_ENTERED", "AMBIGUOUS", "EOD_CLOSED"}
# final_status values the tracker writes after T1 / trailing-stop events. BUGFIX 2026-11-03: these were missing here, so
# every T1_THEN_SL / TRAILING_SL_HIT / T1_HIT_THEN_EOD trade was silently dropped from the ledger (losses under-counted).
TERMINAL_FINAL = TERMINAL | {"T1_THEN_SL", "T1_THEN_TRAILING_SL", "TRAILING_SL_HIT", "T1_HIT_THEN_EOD",
                             "T1_HIT_THEN_AMBIGUOUS"}
STATE_KEYS = (("trades", "live"), ("shadow_trades", "main"), ("fib_pivot_shadow_trades", "fib_pivot"),
              ("orb_pivot_shadow_trades", "orb_pivot"), ("sweep_shadow_trades", "sweep_reversal"))
COLUMNS = ["trade_id", "date", "engine", "strategy", "symbol", "direction", "strike", "expiry",
           "session", "alert_time", "actual_entry_time", "alert_premium", "entry_price",
           "stop_loss", "target_1", "target_2", "final_status", "t1_hit", "result_pct",
           "mfe_pct", "mae_pct", "grade", "trade_score", "wide_prev_day",
           "expiry_day", "entry_oi", "entry_volume", "spread_pct", "cost_pct_assumed", "net_result_pct",
           "be_after_t1_pct", "half_t1_plus_be_pct", "entry_bucket"]


def result_pct(trade: dict):
    entry = trade.get("tracked_entry_price")
    status = trade.get("final_status") or trade.get("status")
    if not entry:
        return None
    sl = trade.get("stop_loss")   # the tracker keeps this at the level actually stopped out (trailing included)
    exit_price = {"SL_HIT": sl, "T2_HIT": trade.get("target_2"), "EOD_CLOSED": trade.get("eod_last_price"),
                  "T1_THEN_SL": sl, "T1_THEN_TRAILING_SL": sl, "TRAILING_SL_HIT": sl,
                  "T1_HIT_THEN_EOD": trade.get("eod_last_price")}.get(status)
    if exit_price is None:
        return None
    return round((exit_price - entry) / entry * 100, 2)


def t1_variants(trade: dict, res):
    """What-if results (price % move on entry, before costs): (SL moved to entry after T1, half booked at T1 + rest on that rule).

    Trades that never hit T1 are unchanged (both equal `res`). Resolving T2_HIT / T1-then-EOD outcomes needs the tracker's
    be_touch_price / be_tracked markers (added 2026-11-03); older records without them return None for those.
    """
    entry = trade.get("tracked_entry_price")
    if res is None or not entry:
        return None, None
    if not trade.get("t1_hit"):
        return res, res
    status = trade.get("final_status") or trade.get("status")
    t1pct = (trade["target_1"] - entry) / entry * 100
    if status in ("T1_THEN_SL", "T1_THEN_TRAILING_SL", "TRAILING_SL_HIT") or trade.get("be_touch_price") is not None:
        rest = 0.0          # price came back to entry (or the stop below it): the breakeven rule exits at entry
    elif trade.get("be_tracked"):
        rest = res          # never returned to entry before the outcome
    else:
        return None, None
    return round(rest, 2), round(0.5 * t1pct + 0.5 * rest, 2)


def net_result_pct(res):
    """result_pct minus the ASSUMED round-trip cost (config.LEDGER_ROUND_TRIP_COST_PCT)."""
    if res is None:
        return None
    return round(res - config.LEDGER_ROUND_TRIP_COST_PCT, 2)


def entry_bucket(alert_time) -> str:
    """Time-of-day bucket of the alert (IST): open / morning / midday / late / '' if unknown."""
    try:
        hm = (int(str(alert_time)[11:13]), int(str(alert_time)[14:16]))
    except Exception:
        return ""
    if hm < (9, 45):
        return "0915-0945"
    if hm < (11, 30):
        return "0945-1130"
    if hm < (14, 0):
        return "1130-1400"
    return "1400+"


def _row(trade: dict, engine: str, date_str: str) -> dict:
    status = trade.get("final_status") or trade.get("status")
    res = result_pct(trade)
    be, half = t1_variants(trade, res)
    return {
        "trade_id": trade["trade_id"], "date": date_str, "engine": engine,
        "strategy": trade.get("strategy"), "symbol": trade.get("symbol"),
        "direction": trade.get("direction"), "strike": trade.get("strike"),
        "expiry": trade.get("expiry"), "session": trade.get("session"),
        "alert_time": trade.get("alert_time"), "actual_entry_time": trade.get("actual_entry_time"),
        "alert_premium": trade.get("alert_premium"), "entry_price": trade.get("tracked_entry_price"),
        "stop_loss": trade.get("stop_loss"), "target_1": trade.get("target_1"),
        "target_2": trade.get("target_2"), "final_status": status, "t1_hit": trade.get("t1_hit"),
        "result_pct": res, "mfe_pct": trade.get("max_favorable_pct"),
        "mae_pct": trade.get("max_adverse_pct"), "grade": trade.get("grade"),
        "trade_score": trade.get("trade_score"), "wide_prev_day": trade.get("wide_prev_day"),
        "expiry_day": (trade.get("expiry") == date_str) if trade.get("expiry") and date_str else None,
        "entry_oi": trade.get("entry_oi"), "entry_volume": trade.get("entry_volume"),
        "spread_pct": trade.get("spread_pct"),
        "cost_pct_assumed": config.LEDGER_ROUND_TRIP_COST_PCT if res is not None else None,
        "net_result_pct": net_result_pct(res),
        "be_after_t1_pct": be, "half_t1_plus_be_pct": half,
        "entry_bucket": entry_bucket(trade.get("alert_time")),
    }


def append_resolved(state: dict, ledger_path: Path) -> int:
    """Append newly resolved trades; returns rows added. Never raises on bad trade records."""
    ledger_path = Path(ledger_path)
    existing = set()
    if ledger_path.exists():
        with open(ledger_path, newline="") as f:
            old_rows = list(csv.DictReader(f))
        existing = {r["trade_id"] for r in old_rows}
        with open(ledger_path, newline="") as f:
            header = next(csv.reader(f), [])
        if header and header != COLUMNS:        # schema grew (new trailing columns): rewrite once with the new header
            with open(ledger_path, "w", newline="") as f:
                w0 = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
                w0.writeheader()
                for r0 in old_rows:
                    r0.setdefault("entry_bucket", entry_bucket(r0.get("alert_time")))
                w0.writerows(old_rows)
    new_rows = []
    for key, engine in STATE_KEYS:
        for trade in (state.get(key) or {}).values():
            try:
                status = trade.get("final_status") or trade.get("status")
                if (status not in TERMINAL_FINAL and trade.get("status") not in TERMINAL) or trade["trade_id"] in existing:
                    continue
                new_rows.append(_row(trade, engine, state.get("date")))
                existing.add(trade["trade_id"])
            except Exception:
                continue
    if not new_rows:
        return 0
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not ledger_path.exists() or ledger_path.stat().st_size == 0
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLUMNS)
    if write_header:
        w.writeheader()
    w.writerows(new_rows)
    with open(ledger_path, "a", newline="") as f:
        f.write(buf.getvalue())
    return len(new_rows)
