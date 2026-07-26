"""
app/engines/replay_analytics/periodic_reports.py -- Daily / Weekly /
Monthly Analytics.
------------------------------------------------------------------------------
Thin rollups over trade_journal.py + performance_analytics.py +
learning_engine.py for a given calendar window. Each report is
persisted via daily_cache.py under its own subdir (config.REPLAY_
DAILY_REPORT_SUBDIR / _WEEKLY_ / _MONTHLY_) so re-running a report for
an already-covered period is cheap and idempotent (same keyed-write
pattern as trade_journal.append_entries()).

Independently toggleable per config.REPLAY_DAILY_ANALYTICS_ENABLED /
_WEEKLY_ / _MONTHLY_ -- disabling one doesn't affect the others or the
underlying journal.
"""

from datetime import datetime, timedelta

import daily_cache
from config import (
    REPLAY_DAILY_REPORT_SUBDIR, REPLAY_WEEKLY_REPORT_SUBDIR, REPLAY_MONTHLY_REPORT_SUBDIR,
    REPLAY_DAILY_ANALYTICS_ENABLED, REPLAY_WEEKLY_ANALYTICS_ENABLED, REPLAY_MONTHLY_ANALYTICS_ENABLED,
)
from . import performance_analytics, trade_journal

_daily_cache = daily_cache.DailyCache(REPLAY_DAILY_REPORT_SUBDIR)
_weekly_cache = daily_cache.DailyCache(REPLAY_WEEKLY_REPORT_SUBDIR)
_monthly_cache = daily_cache.DailyCache(REPLAY_MONTHLY_REPORT_SUBDIR)


def _best_worst_trade(records: list) -> tuple:
    tradeable = [r for r in records if r.get("outcome") not in ("NO_ENTRY", "INVALID_SIGNAL")]
    if not tradeable:
        return None, None
    best = max(tradeable, key=lambda r: r.get("pnl_pct", 0.0))
    worst = min(tradeable, key=lambda r: r.get("pnl_pct", 0.0))
    return best, worst


def generate_daily_summary(date_str: str) -> dict | None:
    if not REPLAY_DAILY_ANALYTICS_ENABLED:
        return None
    records = trade_journal.read_journal(date_str)
    perf = performance_analytics.compute_performance(records)
    best, worst = _best_worst_trade(records)
    rr_values = [r.get("pnl_pct", 0.0) for r in records if r.get("outcome") not in ("NO_ENTRY", "INVALID_SIGNAL")]
    avg_rr = round(sum(rr_values) / len(rr_values), 2) if rr_values else 0.0

    summary = {
        "date": date_str,
        "trades_generated": len(records),
        "trades_accepted": len([r for r in records if r.get("outcome") not in ("NO_ENTRY", "INVALID_SIGNAL")]),
        "trades_rejected": len([r for r in records if r.get("outcome") in ("NO_ENTRY", "INVALID_SIGNAL")]),
        "win_rate": perf["win_rate"],
        "avg_rr": avg_rr,
        "best_trade": {"symbol": best.get("symbol"), "pnl_pct": best.get("pnl_pct")} if best else None,
        "worst_trade": {"symbol": worst.get("symbol"), "pnl_pct": worst.get("pnl_pct")} if worst else None,
        "performance": perf,
    }
    _daily_cache.write(date_str, summary, commit_message=f"replay_analytics: daily summary {date_str}")
    return summary


def _week_bounds(any_date_str: str) -> tuple:
    d = datetime.strptime(any_date_str, "%Y-%m-%d")
    monday = d - timedelta(days=d.weekday())
    sunday = monday + timedelta(days=6)
    return monday.strftime("%Y-%m-%d"), sunday.strftime("%Y-%m-%d")


def generate_weekly_report(any_date_in_week_str: str) -> dict | None:
    if not REPLAY_WEEKLY_ANALYTICS_ENABLED:
        return None
    from . import learning_engine
    start, end = _week_bounds(any_date_in_week_str)
    records = trade_journal.read_journal_range(start, end)
    perf = performance_analytics.compute_performance(records)
    learning = learning_engine.generate_learning_report(records)

    report = {
        "week_start": start, "week_end": end,
        "performance": perf,
        "most_profitable_pattern": learning["breakout_pattern"]["best"],
        "least_profitable_pattern": learning["breakout_pattern"]["worst"],
        "best_market_regime": learning["market_condition"]["best"],
        "worst_market_regime": learning["market_condition"]["worst"],
        "scanner_accuracy": perf["win_rate"],
    }
    _weekly_cache.write(start, report, commit_message=f"replay_analytics: weekly report {start}")
    return report


def _month_bounds(any_date_str: str) -> tuple:
    d = datetime.strptime(any_date_str, "%Y-%m-%d")
    start = d.replace(day=1)
    if start.month == 12:
        next_month = start.replace(year=start.year + 1, month=1)
    else:
        next_month = start.replace(month=start.month + 1)
    end = next_month - timedelta(days=1)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


def generate_monthly_report(any_date_in_month_str: str) -> dict | None:
    if not REPLAY_MONTHLY_ANALYTICS_ENABLED:
        return None
    start, end = _month_bounds(any_date_in_month_str)
    records = trade_journal.read_journal_range(start, end)
    perf = performance_analytics.compute_performance(records)

    confidences = [r.get("confidence") for r in records if r.get("confidence") is not None]
    probabilities = [r.get("probability") for r in records if r.get("probability") is not None]
    qualities = [r.get("trade_quality") for r in records if r.get("trade_quality") is not None]
    profits = [r.get("pnl_pct", 0.0) for r in records if r.get("outcome") == "WIN"]
    losses = [r.get("pnl_pct", 0.0) for r in records if r.get("outcome") == "LOSS"]

    report = {
        "month_start": start, "month_end": end,
        "overall_win_rate": perf["win_rate"],
        "avg_confidence": round(sum(confidences) / len(confidences), 1) if confidences else None,
        "avg_probability": round(sum(probabilities) / len(probabilities), 1) if probabilities else None,
        "avg_trade_quality": round(sum(qualities) / len(qualities), 1) if qualities else None,
        "profit_distribution": sorted(profits),
        "loss_distribution": sorted(losses),
        "performance": perf,
    }
    _monthly_cache.write(start[:7], report, commit_message=f"replay_analytics: monthly report {start[:7]}")
    return report
