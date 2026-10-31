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

TERMINAL = {"T2_HIT", "SL_HIT", "NOT_ENTERED", "AMBIGUOUS", "EOD_CLOSED"}
STATE_KEYS = (("shadow_trades", "main"), ("fib_pivot_shadow_trades", "fib_pivot"),
              ("orb_pivot_shadow_trades", "orb_pivot"))
COLUMNS = ["trade_id", "date", "engine", "strategy", "symbol", "direction", "strike", "expiry",
           "session", "alert_time", "actual_entry_time", "alert_premium", "entry_price",
           "stop_loss", "target_1", "target_2", "final_status", "t1_hit", "result_pct",
           "mfe_pct", "mae_pct", "grade", "trade_score", "wide_prev_day"]


def result_pct(trade: dict):
    entry = trade.get("tracked_entry_price")
    status = trade.get("final_status") or trade.get("status")
    if not entry:
        return None
    exit_price = {"SL_HIT": trade.get("stop_loss"), "T2_HIT": trade.get("target_2"),
                  "EOD_CLOSED": trade.get("eod_last_price")}.get(status)
    if exit_price is None:
        return None
    return round((exit_price - entry) / entry * 100, 2)


def _row(trade: dict, engine: str, date_str: str) -> dict:
    status = trade.get("final_status") or trade.get("status")
    return {
        "trade_id": trade["trade_id"], "date": date_str, "engine": engine,
        "strategy": trade.get("strategy"), "symbol": trade.get("symbol"),
        "direction": trade.get("direction"), "strike": trade.get("strike"),
        "expiry": trade.get("expiry"), "session": trade.get("session"),
        "alert_time": trade.get("alert_time"), "actual_entry_time": trade.get("actual_entry_time"),
        "alert_premium": trade.get("alert_premium"), "entry_price": trade.get("tracked_entry_price"),
        "stop_loss": trade.get("stop_loss"), "target_1": trade.get("target_1"),
        "target_2": trade.get("target_2"), "final_status": status, "t1_hit": trade.get("t1_hit"),
        "result_pct": result_pct(trade), "mfe_pct": trade.get("max_favorable_pct"),
        "mae_pct": trade.get("max_adverse_pct"), "grade": trade.get("grade"),
        "trade_score": trade.get("trade_score"), "wide_prev_day": trade.get("wide_prev_day"),
    }


def append_resolved(state: dict, ledger_path: Path) -> int:
    """Append newly resolved trades; returns rows added. Never raises on bad trade records."""
    ledger_path = Path(ledger_path)
    existing = set()
    if ledger_path.exists():
        with open(ledger_path, newline="") as f:
            existing = {r["trade_id"] for r in csv.DictReader(f)}
    new_rows = []
    for key, engine in STATE_KEYS:
        for trade in (state.get(key) or {}).values():
            try:
                status = trade.get("final_status") or trade.get("status")
                if status not in TERMINAL or trade["trade_id"] in existing:
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
