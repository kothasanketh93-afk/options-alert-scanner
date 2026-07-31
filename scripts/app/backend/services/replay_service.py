"""
replay_service.py
-------------------
Read-only access to what `app/engines/replay_analytics/` has already
computed and persisted. This service NEVER triggers a replay run and
NEVER writes -- `engine.run_replay_analytics()` (candle replay, WIN/
LOSS classification, journal writes) only runs from its own separate
scheduled job (see that module's docstring); this dashboard process
has no candle data and no business running it.

Two different kinds of "read" here, both genuinely read-only:
  - trade_journal.read_journal() / read_journal_range() -- these are
    the existing engine's own read functions, reused as-is.
  - daily_cache.read(...) for the daily/weekly/monthly reports --
    called directly (bypassing periodic_reports.generate_*()) because
    those generate_* functions COMPUTE AND WRITE the report; calling
    them from this read-only process would (a) write to the state
    branch from a process that isn't supposed to, and (b) silently
    create a report that looks "official" but was generated on
    whatever partial data happened to be in the journal at request
    time. If a report hasn't been generated yet by the real
    scheduled job, this service says so rather than faking it.
"""

import daily_cache
from config import (
    REPLAY_ANALYTICS_ENABLED,
    REPLAY_DAILY_REPORT_SUBDIR,
    REPLAY_WEEKLY_REPORT_SUBDIR,
    REPLAY_MONTHLY_REPORT_SUBDIR,
)
from app.engines.replay_analytics import trade_journal
from app.backend.services import state_reader


def _week_monday(date_str: str) -> str:
    from datetime import datetime, timedelta
    d = datetime.strptime(date_str, "%Y-%m-%d")
    monday = d - timedelta(days=d.weekday())
    return monday.strftime("%Y-%m-%d")


def is_enabled() -> bool:
    return REPLAY_ANALYTICS_ENABLED


def get_journal(date_str: str) -> dict:
    if not REPLAY_ANALYTICS_ENABLED:
        return {"date": date_str, "enabled": False, "entries": []}
    state_reader.ensure_state_available()
    entries = trade_journal.read_journal(date_str)
    return {"date": date_str, "enabled": True, "entries": entries, "count": len(entries)}


def get_journal_range(start_date: str, end_date: str) -> dict:
    if not REPLAY_ANALYTICS_ENABLED:
        return {"start_date": start_date, "end_date": end_date, "enabled": False, "entries": []}
    state_reader.ensure_state_available()
    entries = trade_journal.read_journal_range(start_date, end_date)
    return {
        "start_date": start_date, "end_date": end_date,
        "enabled": True, "entries": entries, "count": len(entries),
    }


def get_daily_summary(date_str: str) -> dict:
    state_reader.ensure_state_available()
    cached = daily_cache.read(REPLAY_DAILY_REPORT_SUBDIR, date_str)
    if cached is None:
        return {"date": date_str, "available": False, "reason": "not generated yet for this date"}
    return {"date": date_str, "available": True, "summary": cached}


def get_weekly_summary(any_date_in_week: str) -> dict:
    state_reader.ensure_state_available()
    monday = _week_monday(any_date_in_week)
    cached = daily_cache.read(REPLAY_WEEKLY_REPORT_SUBDIR, monday)
    if cached is None:
        return {"week_start": monday, "available": False, "reason": "not generated yet for this week"}
    return {"week_start": monday, "available": True, "summary": cached}


def get_monthly_summary(year_month: str) -> dict:
    """year_month: 'YYYY-MM'."""
    state_reader.ensure_state_available()
    cached = daily_cache.read(REPLAY_MONTHLY_REPORT_SUBDIR, year_month)
    if cached is None:
        return {"month": year_month, "available": False, "reason": "not generated yet for this month"}
    return {"month": year_month, "available": True, "summary": cached}
