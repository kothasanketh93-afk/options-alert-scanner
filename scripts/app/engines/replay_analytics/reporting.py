"""
app/engines/replay_analytics/reporting.py -- Reporting.
------------------------------------------------------------------------------
Optional, independently-toggleable file exports of whatever's already
in the journal/analytics dicts -- this module does no computation of
its own, only serialization. Every export is gated by its own config
flag (REPLAY_REPORT_CSV_ENABLED / REPLAY_REPORT_JSON_ENABLED) so a
caller that only wants the in-memory summary dict from engine.py never
touches the filesystem.
"""

import csv
import json
from pathlib import Path

from config import REPLAY_REPORT_CSV_ENABLED, REPLAY_REPORT_JSON_ENABLED, REPLAY_REPORT_OUTPUT_DIR
from logging_setup import get_logger

_logger = get_logger("replay_analytics")


def _output_dir() -> Path:
    p = Path(REPLAY_REPORT_OUTPUT_DIR)
    p.mkdir(parents=True, exist_ok=True)
    return p


def write_csv_report(records: list, filename: str) -> str | None:
    if not REPLAY_REPORT_CSV_ENABLED or not records:
        return None
    path = _output_dir() / filename
    flat_rows = []
    for r in records:
        row = {k: v for k, v in r.items() if not isinstance(v, (dict, list))}
        flat_rows.append(row)
    fieldnames = sorted({k for row in flat_rows for k in row.keys()})
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(flat_rows)
    _logger.info("replay_analytics: wrote CSV report %s (%d rows)", path, len(flat_rows))
    return str(path)


def write_json_report(payload: dict, filename: str) -> str | None:
    if not REPLAY_REPORT_JSON_ENABLED:
        return None
    path = _output_dir() / filename
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)
    _logger.info("replay_analytics: wrote JSON report %s", path)
    return str(path)


def summary_text(performance: dict, health: dict = None) -> str:
    """Short human-readable summary -- used for Telegram/log output,
    never for the full report (that's the CSV/JSON exports above)."""
    lines = [
        f"Trades: {performance['total_trades']} | Win Rate: {performance['win_rate']}% | "
        f"Profit Factor: {performance['profit_factor']} | Expectancy: {performance['expectancy_pct']}%",
        f"Avg Win: {performance['avg_profit_pct']}% | Avg Loss: {performance['avg_loss_pct']}% | "
        f"Max Drawdown: {performance['max_drawdown_pct']}%",
        f"Max Consecutive Wins: {performance['max_consecutive_wins']} | "
        f"Max Consecutive Losses: {performance['max_consecutive_losses']}",
    ]
    if health:
        lines.append(f"Scanner Health: {health['scanner_health_score']}/100 ({health['band']})")
    return "\n".join(lines)
