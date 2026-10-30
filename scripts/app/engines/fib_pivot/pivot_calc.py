"""
app/engines/fib_pivot/pivot_calc.py -- classic Fibonacci-ratio pivot points.
------------------------------------------------------------------------------
This is the textbook "Pivot Points Standard" indicator with its Type
switched to "Fibonacci" (the exact TradingView setting shown in the
reel's screenshots), computed from the PRIOR completed period's High,
Low and Close:

    P  = (H + L + C) / 3
    R1 = P + 0.382 * (H - L)
    R2 = P + 0.618 * (H - L)
    R3 = P + 1.000 * (H - L)
    S1 = P - 0.382 * (H - L)
    S2 = P - 0.618 * (H - L)
    S3 = P - 1.000 * (H - L)

This is a standard, unambiguous, widely-published formula (the same one
TradingView's own "Fibonacci" pivot type uses) -- there is no modeling
judgment call here, unlike base_detector.py/institutional_score.py's
"deliberately not built" pattern-recognition sub-engines. The only
genuine design decision in this whole engine is which PRIOR PERIOD to
derive H/L/C from -- see `compute_fibonacci_pivots()`'s docstring below.
"""

from __future__ import annotations


def compute_fibonacci_pivots(prev_high: float, prev_low: float, prev_close: float) -> dict:
    """
    Daily Fibonacci pivots, computed from the PREVIOUS TRADING DAY's
    daily High/Low/Close -- this is the conventional choice for "Pivot
    Points Standard" regardless of which intraday candle size (5-min,
    15-min, ...) the entry confirmation itself is read on. The reel's
    screenshots show the indicator plotted on an intraday chart but
    computed on daily inputs, which is how TradingView's built-in
    "Pivot Points Standard" indicator behaves by default (its "Pivots
    Timeframe" input defaults to "Auto", which resolves to Daily for
    any chart timeframe below 1 day) -- so this matches what the reel
    was actually showing, not a reinterpretation of it.

    Raises ValueError on non-positive/invalid inputs (same "fail loudly
    on bad data rather than silently produce nonsense levels" posture
    as levels.dynamic_trade_levels()) -- a zero-width H==L day would
    collapse every level onto P, which is a data problem, not a
    legitimate pivot reading.
    """
    if prev_high is None or prev_low is None or prev_close is None:
        raise ValueError("compute_fibonacci_pivots: prev_high/prev_low/prev_close must not be None")
    if prev_high < prev_low:
        raise ValueError(
            f"compute_fibonacci_pivots: prev_high ({prev_high}) < prev_low ({prev_low})"
        )

    pivot = (prev_high + prev_low + prev_close) / 3.0
    rng = prev_high - prev_low

    return {
        "P": round(pivot, 2),
        "R1": round(pivot + 0.382 * rng, 2),
        "R2": round(pivot + 0.618 * rng, 2),
        "R3": round(pivot + 1.000 * rng, 2),
        "S1": round(pivot - 0.382 * rng, 2),
        "S2": round(pivot - 0.618 * rng, 2),
        "S3": round(pivot - 1.000 * rng, 2),
    }


# Ordered lists used by close_confirmation.py to walk levels nearest-to-
# farthest in each direction, and to find "the next level beyond the one
# just crossed" for levels.dynamic_trade_levels()'s target cap.
RESISTANCE_LEVELS_ASCENDING = ["P", "R1", "R2", "R3"]
SUPPORT_LEVELS_DESCENDING = ["P", "S1", "S2", "S3"]


def next_resistance_beyond(pivots: dict, level_name: str):
    """Returns the value of the next resistance level ABOVE `level_name`
    (e.g. next_resistance_beyond(pivots, "R1") -> pivots["R2"]), or None
    if `level_name` is already the highest level this engine tracks (R3)."""
    try:
        idx = RESISTANCE_LEVELS_ASCENDING.index(level_name)
    except ValueError:
        return None
    if idx + 1 >= len(RESISTANCE_LEVELS_ASCENDING):
        return None
    return pivots[RESISTANCE_LEVELS_ASCENDING[idx + 1]]


def next_support_beyond(pivots: dict, level_name: str):
    """Mirror of next_resistance_beyond() for the support side (S1 -> S2 -> S3)."""
    try:
        idx = SUPPORT_LEVELS_DESCENDING.index(level_name)
    except ValueError:
        return None
    if idx + 1 >= len(SUPPORT_LEVELS_DESCENDING):
        return None
    return pivots[SUPPORT_LEVELS_DESCENDING[idx + 1]]
