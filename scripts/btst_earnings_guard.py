"""
btst_earnings_guard.py
-----------------------
Blocks a stock from BTST entry if it has a known earnings/board-meeting
date landing on the overnight-carry window (entry evening -> exit
morning). Overnight index gaps come from broad market cues; overnight
STOCK gaps are far more often driven by a specific scheduled event --
this is the single highest-value guardrail for including stocks in
BTST at all.

DELIBERATELY MANUAL, same pattern as holiday_guard.py's
NSE_MOVABLE_HOLIDAYS_<year> set: no live NSE corporate-announcements
API call is wired in here (this project has no verified, stable
public endpoint for that the way it does for the instrument masters --
see verify_stock_symbols.py's approach of only using documented public
files). BLACKOUT_DATES below needs a human to populate from NSE's
corporate announcements page (nseindia.com/companies-listing/
corporate-filings-event-calendar) for each BTST_STOCK_SYMBOLS name,
covering the current quarter, before this guardrail does anything.

An EMPTY or missing entry for a symbol is NOT treated as "confirmed
safe" -- see is_blackout()'s docstring.
"""

from datetime import datetime, timedelta

# symbol -> set of "YYYY-MM-DD" dates (results/board-meeting dates).
# TODO (recurring, quarterly): update from NSE's event calendar for
# every symbol in btst_config.BTST_STOCK_SYMBOLS. Leave a symbol out
# entirely (not an empty set) once you've actually checked it has
# nothing pending -- see is_blackout()'s docstring for why the
# distinction matters.
BLACKOUT_DATES = {
    # "RELIANCE": {"2026-07-25"},
}


def is_blackout(symbol: str, entry_date: str, exit_date: str) -> bool:
    """
    True if `symbol` has a known event date on entry_date, exit_date,
    or the calendar day between them (covers an event announced after
    entry-day's market close but before exit-day's open).

    IMPORTANT: a symbol with NO entry in BLACKOUT_DATES is NOT treated
    as confirmed-safe -- it means "not yet checked". btst_scanner.py
    treats an unpopulated list as a reason to log a loud warning, not
    as silent all-clear. This function itself only answers "is this
    date range in the list", not "has this been verified" -- see
    btst_scanner.py's caller for the warning.
    """
    dates = BLACKOUT_DATES.get(symbol)
    if not dates:
        return False
    entry_dt = datetime.strptime(entry_date, "%Y-%m-%d")
    exit_dt = datetime.strptime(exit_date, "%Y-%m-%d")
    d = entry_dt
    while d <= exit_dt:
        if d.strftime("%Y-%m-%d") in dates:
            return True
        d += timedelta(days=1)
    return False


def is_configured(symbol: str) -> bool:
    """Has a human actually reviewed this symbol's calendar (even if
    the result was 'nothing pending', represented as an explicit empty
    set)? Distinguishes 'checked, clear' from 'never checked'."""
    return symbol in BLACKOUT_DATES
