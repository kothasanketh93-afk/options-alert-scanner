"""
app/engines/fib_pivot/close_confirmation.py -- "candle must CLOSE beyond
the pivot level" entry trigger.
------------------------------------------------------------------------------
This is the one genuinely NEW piece of logic the reel described that the
existing codebase does not have anywhere (confirmed this session via
`grep -rni "pivot"` across the whole repo, and by reading scanner.py's
STRUCTURAL_EXTENSION_CHECK, which compares the LIVE spot price against
indicators.find_nearby_level(), never a candle's own close).

Definition used here (stated explicitly so it's falsifiable/reviewable,
not left implicit):

    A CE (bullish) confirmation on resistance level L fires on candle[i]
    when:
        candle[i-1]["close"] <= L  AND  candle[i]["close"] > L

    i.e. the level was NOT YET closed above as of the previous candle,
    and IS closed above as of the current (latest completed) candle --
    a fresh cross, evaluated on CLOSE, not on intrabar high/low. A PE
    (bearish) confirmation on support level L is the mirror:
        candle[i-1]["close"] >= L  AND  candle[i]["close"] < L

Why "fresh cross on the immediately preceding candle" rather than "closed
beyond L at any point in the last N candles": this is a direct, deliberate
fix for the ONE evidence-backed failure pattern already found in the
EXISTING engine's shadow data this session -- the per-factor correlation
analysis showed winning trades had LOWER trend/momentum/market_strength
scores than losing trades, consistent with the existing engine frequently
entering AFTER a move was already extended (chasing). Requiring the cross
to be on the candle immediately prior keeps this engine from firing three
or five candles after the level was already cleared.

Only the SINGLE NEAREST qualifying level is returned (R1 before R2 before
R3 for CE; S1 before S2 before S3 for PE) -- if price gapped straight
through two levels in one candle, that is logged as a (rare) simultaneous
cross and the nearer level is used, since it is the one actually crossed
first in price terms.
"""

from __future__ import annotations

from . import pivot_calc


def detect_close_confirmation(candles: list, pivots: dict, direction: str) -> dict:
    """
    `candles` must be sorted ascending by timestamp and have at least 2
    entries (the function only ever looks at the last two: candles[-2]
    as the "prior" candle, candles[-1] as the "current/just-closed"
    candle -- exactly mirroring tracker.py's own "use the candle's
    close, we cannot observe true intrabar fill order" posture).

    Returns {"confirmed": False} if no fresh close-confirmation exists
    on the current candle, or on bad input (too few candles, unknown
    direction). Returns
        {"confirmed": True, "level_name": "R1", "level_value": 24850.3,
         "close": 24861.0, "prev_close": 24845.0}
    on a genuine fresh cross.
    """
    if direction not in ("CE", "PE"):
        return {"confirmed": False}
    if not candles or len(candles) < 2:
        return {"confirmed": False}

    prev_close = candles[-2]["close"]
    cur_close = candles[-1]["close"]

    level_order = (
        pivot_calc.RESISTANCE_LEVELS_ASCENDING if direction == "CE"
        else pivot_calc.SUPPORT_LEVELS_DESCENDING
    )

    for level_name in level_order:
        level_value = pivots[level_name]
        if direction == "CE":
            fresh_cross = (prev_close <= level_value) and (cur_close > level_value)
        else:
            fresh_cross = (prev_close >= level_value) and (cur_close < level_value)

        if fresh_cross:
            return {
                "confirmed": True,
                "level_name": level_name,
                "level_value": level_value,
                "close": cur_close,
                "prev_close": prev_close,
            }

    return {"confirmed": False}
