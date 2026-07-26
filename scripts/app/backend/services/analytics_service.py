"""
analytics_service.py
----------------------
On-demand analytics computed from trade_journal entries. Unlike
periodic_reports.generate_daily/weekly/monthly_summary() (which
compute AND persist a report), everything here is a pure read +
pure-function computation with no write -- safe to call for an
arbitrary caller-supplied date range without creating a new "official"
report file each time someone opens the dashboard.

Reuses performance_analytics.compute_performance() and
health_engine.compute_health_score() as-is (both already pure
functions over a list of journal-shaped records -- no changes needed
to either).
"""

from config import REPLAY_ANALYTICS_ENABLED
from app.engines.replay_analytics import trade_journal, performance_analytics, health_engine
from app.backend.services import state_reader


def _journal_range(start_date: str, end_date: str) -> list:
    state_reader.ensure_state_available()
    return trade_journal.read_journal_range(start_date, end_date)


def get_performance(start_date: str, end_date: str) -> dict:
    """Win rate, profit factor, expectancy, drawdown, streaks --
    exactly performance_analytics.compute_performance()'s fields,
    the same numbers the Part 8 spec's Trade Analytics section asks
    for (Total/Winning/Losing Trades, Avg holding time, Max Drawdown,
    Profit Factor, Expectancy)."""
    if not REPLAY_ANALYTICS_ENABLED:
        return {"start_date": start_date, "end_date": end_date, "enabled": False}
    records = _journal_range(start_date, end_date)
    return {
        "start_date": start_date, "end_date": end_date,
        "enabled": True, "sample_size": len(records),
        "performance": performance_analytics.compute_performance(records),
    }


def get_trade_statistics(start_date: str, end_date: str) -> dict:
    """Adds average confidence/probability/trade_quality on top of
    get_performance() -- same fields periodic_reports.
    generate_monthly_report() derives, just computed on demand for an
    arbitrary range instead of only a calendar month, and without
    persisting a report."""
    if not REPLAY_ANALYTICS_ENABLED:
        return {"start_date": start_date, "end_date": end_date, "enabled": False}
    records = _journal_range(start_date, end_date)

    confidences = [r["confidence"] for r in records if r.get("confidence") is not None]
    probabilities = [r["probability"] for r in records if r.get("probability") is not None]
    qualities = [r["trade_quality"] for r in records if r.get("trade_quality") is not None]

    perf = performance_analytics.compute_performance(records)

    return {
        "start_date": start_date, "end_date": end_date,
        "enabled": True, "sample_size": len(records),
        "avg_confidence": round(sum(confidences) / len(confidences), 1) if confidences else None,
        "avg_probability": round(sum(probabilities) / len(probabilities), 1) if probabilities else None,
        "avg_trade_quality": round(sum(qualities) / len(qualities), 1) if qualities else None,
        "performance": perf,
    }


def get_scanner_health(start_date: str, end_date: str) -> dict:
    """
    Scanner Health Score over a date range, using health_engine.
    compute_health_score() as-is.

    HONEST LIMITATION: that function's "false negative rate" and
    "good trades rejected" components need the day's REJECTED
    candidates and missed_opportunity findings, which only exist
    inside the offline replay job's own run (it has the candle data
    to evaluate rejected setups; this dashboard process does not).
    Called here with only the journal's alerted/accepted trades, so
    false_negative_rate always reports 0.0 and decision_accuracy /
    signal_accuracy / false_positive_rate are the only components
    that reflect real data. This is stated in the response itself,
    not just in this comment.
    """
    if not REPLAY_ANALYTICS_ENABLED:
        return {"start_date": start_date, "end_date": end_date, "enabled": False}
    records = _journal_range(start_date, end_date)
    health = health_engine.compute_health_score(records)
    return {
        "start_date": start_date, "end_date": end_date,
        "enabled": True,
        "health": health,
        "limitation": (
            "false_negative_rate is not computed by this endpoint -- it needs "
            "rejected-candidate and missed-opportunity data that only exists "
            "inside the offline replay job's own run, not in this read-only "
            "dashboard process."
        ),
    }
