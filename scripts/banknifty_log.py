"""BankNifty-only trade log: one CSV row per BankNifty alert/trade, with exit and P&L after a realistic cost estimate.

Built from the outcomes ledger (ledger/outcomes.csv) after each run's resolved trades are appended, so it needs no extra tracking.
Output (private state repo): banknifty/trade_log.csv, fully rebuilt each time (idempotent). P&L is for ONE lot (config.INDEX_LOT_SIZE).
net = gross - charges (cost_model.charges_rs) - spread/slippage allowance (cost_model.slippage_rs). Estimates, not contract notes.
"""
import csv
from datetime import date as _date
from pathlib import Path

import config
import cost_model

SYMBOL = "BANKNIFTY"
LOG_REL = Path("banknifty") / "trade_log.csv"
COLUMNS = ["date", "trade_id", "kind", "engine", "strategy", "alert_time", "spot_at_alert", "strike", "option_type", "expiry",
           "days_to_expiry", "entry_price", "stop_loss", "target_1", "target_2", "exit_price", "exit_reason", "exit_time",
           "hold_min", "lot_size", "gross_pnl_rs", "charges_rs", "slippage_rs", "net_pnl_rs", "net_pnl_pct",
           "detect_to_send_sec", "signal_to_send_sec"]


def _f(x):
    try:
        return float(x) if x not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _dte(date_s, expiry_s):
    try:
        return (_date.fromisoformat(expiry_s) - _date.fromisoformat(date_s)).days
    except (TypeError, ValueError):
        return None


def to_log_row(r: dict, lot_size: int = None) -> dict:
    """One outcomes-ledger row (dict of strings) -> trade-log row."""
    lot = int(lot_size or config.INDEX_LOT_SIZE.get(SYMBOL, 30))
    status = r.get("final_status")
    entered = status not in (None, "", "NOT_ENTERED") and _f(r.get("result_pct")) is not None and _f(r.get("entry_price")) is not None
    pnl = cost_model.trade_pnl(_f(r.get("entry_price")), _f(r.get("result_pct")), lot) if entered else {}
    entry_value = (_f(r.get("entry_price")) or 0) * lot
    return {
        "date": r.get("date"), "trade_id": r.get("trade_id"), "kind": "live" if r.get("engine") == "live" else "shadow",
        "engine": r.get("engine"), "strategy": r.get("strategy"), "alert_time": r.get("alert_time"),
        "spot_at_alert": r.get("spot_at_alert"), "strike": r.get("strike"), "option_type": r.get("direction"),
        "expiry": r.get("expiry"), "days_to_expiry": _dte(r.get("date"), r.get("expiry")),
        "entry_price": r.get("entry_price") if entered else r.get("alert_premium"), "stop_loss": r.get("stop_loss"),
        "target_1": r.get("target_1"), "target_2": r.get("target_2"),
        "exit_price": pnl.get("exit_price") if entered else None, "exit_reason": status, "exit_time": r.get("exit_time"),
        "hold_min": r.get("hold_min"), "lot_size": lot, "gross_pnl_rs": pnl.get("gross"), "charges_rs": pnl.get("charges"),
        "slippage_rs": pnl.get("slippage"), "net_pnl_rs": pnl.get("net"),
        "net_pnl_pct": round(pnl["net"] / entry_value * 100, 2) if entered and pnl.get("net") is not None and entry_value else None,
        "detect_to_send_sec": r.get("detect_to_send_sec"), "signal_to_send_sec": r.get("signal_to_send_sec"),
    }


def read_log(path) -> list:
    path = Path(path)
    if not path.exists():
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def rebuild(ledger_csv, out_csv) -> int:
    """Rebuild the BankNifty log from the outcomes ledger. Returns the number of rows. Never raises."""
    try:
        ledger_csv, out_csv = Path(ledger_csv), Path(out_csv)
        if not ledger_csv.exists():
            return 0
        with open(ledger_csv, newline="") as f:
            rows = [to_log_row(r) for r in csv.DictReader(f) if r.get("symbol") == SYMBOL]
        rows.sort(key=lambda r: (str(r["date"]), str(r["alert_time"])))
        out_csv.parent.mkdir(parents=True, exist_ok=True)
        with open(out_csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=COLUMNS)
            w.writeheader()
            w.writerows(rows)
        return len(rows)
    except Exception:
        return 0
