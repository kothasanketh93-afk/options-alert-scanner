"""
app/engines/early_breakout/fair_value_gap.py -- Fair Value Gap (FVG)
detection, added 2026-07-25 as a scoped pilot slice of the requested
"Institutional Intelligence Engine" (see engine.py's module docstring
for why only this one piece was built, not all 15 requested sub-engines).
------------------------------------------------------------------------------
Definition (unambiguous, standard 3-candle imbalance -- deliberately
picked for this pilot BECAUSE it's this crisp, unlike order blocks/BOS-
CHoCH/liquidity sweeps, which have several competing informal
definitions depending on which SMC source you read):

  Bullish FVG at candle i (for i-2, i-1, i):
    candle[i-2].high < candle[i].low
    -- i.e. candle i-1's range (the middle, impulsive candle) left a
    gap between the candle before it and the candle after it that
    price never traded through. The gap is [candle[i-2].high, candle[i].low].

  Bearish FVG at candle i:
    candle[i-2].low > candle[i].high
    -- gap is [candle[i].high, candle[i-2].low].

A gap is "filled" once any LATER candle's range trades back into it
(high >= gap_low and low <= gap_high for a bullish gap, mirrored for
bearish). An unfilled gap in the trade's own direction, formed at or
near the breakout, is read as confirmation that the breakout candle
left a genuine imbalance behind it (unfilled = no one's traded back
through it yet) -- consistent with the "hidden/aggressive buying left
no time for a two-sided market" idea in the spec's Smart Money section,
using only candle data already fetched (no new API calls).
"""

from config import FVG_MIN_SIZE_ATR_FRACTION


def detect_fair_value_gaps(candles: list, atr: float) -> list:
    """
    Scans `candles` for every 3-candle FVG pattern. Returns a list of
    dicts, oldest first:
      {"direction": "CE"|"PE", "index": i, "gap_low": float,
       "gap_high": float, "filled": bool}
    `index` is the index (into `candles`) of the THIRD candle in the
    pattern (the one whose low/high defines the gap edge) -- so
    age-since-formation can be computed as len(candles) - 1 - index,
    matching breakout_detector.py's age convention.

    Gaps smaller than FVG_MIN_SIZE_ATR_FRACTION * atr are skipped --
    without a floor, near-zero-width "gaps" from ordinary candle noise
    would show up constantly and mean nothing.
    """
    gaps = []
    if atr is None or atr <= 0 or len(candles) < 3:
        return gaps

    min_size = atr * FVG_MIN_SIZE_ATR_FRACTION

    for i in range(2, len(candles)):
        c0, c2 = candles[i - 2], candles[i]
        if c0["high"] < c2["low"] and (c2["low"] - c0["high"]) >= min_size:
            gap_low, gap_high = c0["high"], c2["low"]
            filled = any(
                c["high"] >= gap_low and c["low"] <= gap_high
                for c in candles[i + 1:]
            )
            gaps.append({"direction": "CE", "index": i, "gap_low": gap_low, "gap_high": gap_high, "filled": filled})
        elif c0["low"] > c2["high"] and (c0["low"] - c2["high"]) >= min_size:
            gap_low, gap_high = c2["high"], c0["low"]
            filled = any(
                c["high"] >= gap_low and c["low"] <= gap_high
                for c in candles[i + 1:]
            )
            gaps.append({"direction": "PE", "index": i, "gap_low": gap_low, "gap_high": gap_high, "filled": filled})

    return gaps


def fvg_confirmation(candles: list, direction: str, atr: float, breakout_age: int, max_age_gap: int = 5) -> dict:
    """
    Looks for an UNFILLED FVG, in the trade's own direction, formed
    within `max_age_gap` candles of the breakout (i.e. part of the same
    move, not an old, unrelated gap from earlier in the session).

    Returns {"confirmed": bool, "gap": dict|None} -- "confirmed" is
    what engine.py reads to decide whether to log/bonus this; the raw
    gap (if any) is included for the Telegram reasons text.
    """
    gaps = detect_fair_value_gaps(candles, atr)
    newest_index = len(candles) - 1
    for gap in reversed(gaps):  # newest first
        if gap["direction"] != direction or gap["filled"]:
            continue
        age = newest_index - gap["index"]
        if 0 <= age <= max_age_gap + breakout_age:
            return {"confirmed": True, "gap": gap}
    return {"confirmed": False, "gap": None}
