"""
holiday_guard.py
-----------------
Sets should_run=false on weekends and NSE holidays so main.py never
executes (and never wastes an Actions minute) on non-trading days.

You mentioned you already have weekend + NSE holiday protection in
your existing scanner — port that exact holiday list into
NSE_HOLIDAYS below so both systems stay in sync. This file is a thin
GitHub Actions output wrapper around that same logic.
"""

import os
from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))

# TODO: replace with your existing, maintained NSE holiday list.
NSE_HOLIDAYS_2026 = {
    "2026-01-26", "2026-03-06", "2026-03-25", "2026-04-03",
    "2026-04-14", "2026-05-01", "2026-08-15", "2026-10-02",
    "2026-10-21", "2026-11-04", "2026-12-25",
    # ... fill in the rest from your existing list
}


def should_run() -> bool:
    now = datetime.now(IST)
    if now.weekday() >= 5:  # Sat=5, Sun=6
        return False
    if now.strftime("%Y-%m-%d") in NSE_HOLIDAYS_2026:
        return False
    return True


if __name__ == "__main__":
    result = should_run()
    gh_output = os.environ.get("GITHUB_OUTPUT")
    if gh_output:
        with open(gh_output, "a") as f:
            f.write(f"should_run={'true' if result else 'false'}\n")
    print(f"should_run={result}")
