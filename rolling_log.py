"""
rolling_log.py
---------------
A deliberately small, bounded log -- NOT a backtest database. Appends
one line per resolved trade (grade + outcome only, nothing else) to
logs/rolling_log.jsonl on the same `state` git branch, and trims
anything older than ROLLING_LOG_DAYS on every write so it never grows
unbounded.

Brought back after being removed earlier for being "name sake" --
reconsidered because there's no way to know whether
example_quality_score()'s weights (or your own scoring logic) are
actually producing better outcomes for A+ trades than B trades without
SOME feedback mechanism. This is the minimum viable version: grade +
outcome, nothing else, auto-trimmed, read by weekly_report.py.

If you ever want zero retention again, delete this file and remove
its call in main.py.
"""

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import state as state_mod
from config import ROLLING_LOG_DAYS

IST = timezone(timedelta(hours=5, minutes=30))
LOG_PATH_REL = "logs/rolling_log.jsonl"


def _log_path() -> Path:
    return state_mod.STATE_CLONE_DIR / LOG_PATH_REL


def append_resolved_trade(trade: dict, now: datetime = None):
    now = now or state_mod.ist_now()
    entry = {
        "date": trade.get("alert_time", now.isoformat())[:10],
        "trade_id": trade["trade_id"],
        "symbol": trade["symbol"],
        "direction": trade["direction"],
        "moneyness": trade.get("moneyness", "ATM"),
        "grade": trade.get("grade"),
        "status": trade["status"],
        "final_status": trade.get("final_status"),
    }

    path = _log_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    lines = []
    if path.exists():
        with open(path, "r") as f:
            lines = [json.loads(l) for l in f if l.strip()]

    cutoff = (now - timedelta(days=ROLLING_LOG_DAYS)).strftime("%Y-%m-%d")
    lines = [l for l in lines if l["date"] >= cutoff]
    lines.append(entry)

    with open(path, "w") as f:
        for l in lines:
            f.write(json.dumps(l) + "\n")


def read_rolling_log() -> list:
    path = _log_path()
    if not path.exists():
        return []
    with open(path, "r") as f:
        return [json.loads(l) for l in f if l.strip()]
