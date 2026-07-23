"""
btst_state.py
--------------
Same-branch, different-subdir persistence for BTST positions, built
entirely on daily_cache.py's existing read()/write() (which in turn
reuses state.py's battle-tested git clone/pull/push-retry machinery --
see daily_cache.py's own docstring). No new git logic here at all.

Layout: <state-branch>/btst/<entry-date>.json -- one file per BTST
ENTRY date, holding every position opened that evening. This is
DELIBERATE, not a limitation: a BTST position's identity is "entry
date", not "the trading day it happens to resolve on" -- exit-day
logic (btst_tracker.py) reads and re-writes the SAME file the next
morning, rather than creating a second file, so a single position's
full lifecycle always lives in one place.

Never touches state.STATE_DIR ("state/<date>.json", the intraday
file) or any key inside it -- the intraday system has no way to even
observe that this file exists.
"""

import daily_cache

SUBDIR = "btst"


def _fresh(entry_date: str) -> dict:
    return {
        "entry_date": entry_date,
        "positions": {},  # position_id -> position dict, see btst_scanner.new_position()
    }


def load(entry_date: str) -> dict:
    payload = daily_cache.read(SUBDIR, entry_date, compressed=False)
    return payload if payload is not None else _fresh(entry_date)


def save(btst_state: dict, commit_message: str) -> bool:
    """Returns True/False rather than raising -- same non-fatal posture
    as daily_cache.write() itself. Caller (btst_main.py) logs a failure
    loudly but a missed BTST save is not worth failing the whole run
    over, unlike state.save_state()'s intraday trades."""
    return daily_cache.write(
        SUBDIR, btst_state["entry_date"], btst_state, commit_message, compressed=False,
    )


def open_position_count(btst_state: dict, symbol: str = None) -> int:
    positions = btst_state["positions"].values()
    return sum(
        1 for p in positions
        if p["status"] == "OPEN" and (symbol is None or p["symbol"] == symbol)
    )


def has_open_position(btst_state: dict, symbol: str, direction: str) -> bool:
    return any(
        p["symbol"] == symbol and p["direction"] == direction and p["status"] == "OPEN"
        for p in btst_state["positions"].values()
    )
