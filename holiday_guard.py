"""
holiday_guard.py
-----------------
Sets should_run=false on weekends and NSE holidays so main.py never
executes (and never wastes an Actions minute) on non-trading days.

Holidays are split into two categories:

1. FIXED_DATE_HOLIDAYS_MMDD -- national holidays that fall on the same
   Gregorian calendar date every single year, by law/convention
   (Republic Day, Independence Day, Gandhi Jayanti, Maharashtra Day,
   Christmas). These are computed automatically below and NEVER need
   updating, regardless of what year it is.

2. NSE_MOVABLE_HOLIDAYS_<year> -- exchange holidays tied to lunar/
   lunisolar festivals (Holi, Ram Navami, Mahavir Jayanti, Dussehra,
   Diwali, etc.) that genuinely shift to a different Gregorian date
   every year and cannot be computed in advance. This is the one part
   of the whole project that realistically needs a human to update --
   once a year, a handful of dates, from NSE's published holiday
   calendar (published every December for the following year).

Splitting it this way means roughly half of the original list (the
fixed-date half) never needs touching again, and the part that does
need annual attention is clearly isolated and easy to find.
"""

import os
from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))

# Fixed Gregorian-calendar-date national holidays -- same date every
# year, computed automatically, never need updating.
FIXED_DATE_HOLIDAYS_MMDD = {
    "01-26",  # Republic Day
    "04-14",  # Dr. Baba Saheb Ambedkar Jayanti (his birth date, not a
              # lunar festival -- fixed Gregorian date every year, so it
              # belongs here rather than in the movable per-year set)
    "05-01",  # Maharashtra Day
    "08-15",  # Independence Day
    "10-02",  # Gandhi Jayanti
    "12-25",  # Christmas
}

# Movable/lunar-calendar NSE holidays for the CURRENT year only. TODO
# once a year (in December, when NSE publishes next year's calendar):
# add a new NSE_MOVABLE_HOLIDAYS_<year> set below with next year's
# movable-holiday dates, and update NSE_HOLIDAYS_YEAR to match. Do NOT
# delete old years' sets -- harmless to keep, useful if you ever need
# to check historical behavior.
#
# 2026 list below is taken verbatim from NSE's official circular
# (Download Ref No: NSE/CMTR/71775, dated December 12, 2025 --
# https://nsearchives.nseindia.com/content/circulars/CMTR71775.pdf),
# weekday holidays only. The circular also lists four holidays that
# fall on a Saturday/Sunday in 2026 (Mahashivratri Feb 15, Id-Ul-Fitr
# Mar 21, Independence Day Aug 15, Diwali Laxmi Pujan Nov 8) --
# deliberately NOT added here since the weekend check above already
# skips those days; Nov 8's special evening Muhurat Trading session
# falls outside this bot's 09:15-15:30 session windows anyway.
NSE_HOLIDAYS_YEAR = 2026
NSE_MOVABLE_HOLIDAYS_2026 = {
    "2026-03-03",  # Holi
    "2026-03-26",  # Shri Ram Navami
    "2026-03-31",  # Shri Mahavir Jayanti
    "2026-04-03",  # Good Friday
    "2026-05-28",  # Bakri Id
    "2026-06-26",  # Muharram
    "2026-09-14",  # Ganesh Chaturthi
    "2026-10-20",  # Dussehra
    "2026-11-10",  # Diwali-Balipratipada
    "2026-11-24",  # Prakash Gurpurb Sri Guru Nanak Dev
}


def _is_fixed_date_holiday(now: datetime) -> bool:
    return now.strftime("%m-%d") in FIXED_DATE_HOLIDAYS_MMDD


def should_run() -> bool:
    now = datetime.now(IST)
    if now.weekday() >= 5:  # Sat=5, Sun=6
        return False
    if _is_fixed_date_holiday(now):
        return False
    if now.year != NSE_HOLIDAYS_YEAR:
        # The movable-holiday set is year-specific and provides no
        # protection for any year other than NSE_HOLIDAYS_YEAR. Fixed-
        # date holidays above are still caught correctly regardless of
        # year -- only the movable ones are affected. A loud warning
        # doesn't fix the list (only next year's actual dates can), but
        # turns a silent gap into a visible one in the job log.
        print(
            f"[holiday_guard] WARNING: NSE_MOVABLE_HOLIDAYS_{NSE_HOLIDAYS_YEAR} is "
            f"for {NSE_HOLIDAYS_YEAR}, but today is {now.year} -- movable-holiday "
            f"protection is stale this year (fixed-date holidays above are still "
            f"caught correctly). Add this year's movable holidays from NSE's "
            f"published calendar."
        )
        return True
    if now.strftime("%Y-%m-%d") in NSE_MOVABLE_HOLIDAYS_2026:
        return False
    return True


if __name__ == "__main__":
    result = should_run()
    gh_output = os.environ.get("GITHUB_OUTPUT")
    if gh_output:
        with open(gh_output, "a") as f:
            f.write(f"should_run={'true' if result else 'false'}\n")
    print(f"should_run={result}")
