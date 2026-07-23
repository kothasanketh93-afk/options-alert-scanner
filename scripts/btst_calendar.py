"""
btst_calendar.py
-----------------
"What's the next NSE trading day after this one?" -- needed for BTST's
exit-day tracking and for btst_earnings_guard.py's blackout window.
Reuses holiday_guard.py's existing FIXED_DATE_HOLIDAYS_MMDD /
NSE_MOVABLE_HOLIDAYS_<year> data rather than a second copy of the
holiday list that could drift out of sync with it.
"""

from datetime import datetime, timedelta

import holiday_guard


def is_trading_day(dt: datetime) -> bool:
    if dt.weekday() >= 5:
        return False
    if dt.strftime("%m-%d") in holiday_guard.FIXED_DATE_HOLIDAYS_MMDD:
        return False
    if dt.year == holiday_guard.NSE_HOLIDAYS_YEAR:
        movable = getattr(holiday_guard, f"NSE_MOVABLE_HOLIDAYS_{dt.year}", set())
        if dt.strftime("%Y-%m-%d") in movable:
            return False
    # Note: same "stale movable list" gap holiday_guard.should_run()
    # itself warns about for any year other than NSE_HOLIDAYS_YEAR --
    # inherited here, not introduced by this file.
    return True


def next_trading_day(from_date: datetime, max_lookahead: int = 10) -> datetime:
    d = from_date + timedelta(days=1)
    for _ in range(max_lookahead):
        if is_trading_day(d):
            return d
        d += timedelta(days=1)
    raise RuntimeError(
        f"btst_calendar: no trading day found within {max_lookahead} days of "
        f"{from_date.strftime('%Y-%m-%d')} -- holiday_guard's data is likely stale."
    )


def previous_trading_day(from_date: datetime, max_lookback: int = 10) -> datetime:
    d = from_date - timedelta(days=1)
    for _ in range(max_lookback):
        if is_trading_day(d):
            return d
        d -= timedelta(days=1)
    raise RuntimeError(
        f"btst_calendar: no trading day found within {max_lookback} days before "
        f"{from_date.strftime('%Y-%m-%d')} -- holiday_guard's data is likely stale."
    )
