"""
app/engines/early_breakout/breakout_detector.py -- Stages 4 and the Extended-Move filter
of the Early Breakout Engine (upgraded 2026-07-24 for the Institutional
Accumulation spec: backward-searching "first breakout candle" detection
plus the Fresh Breakout Score age decay, and a tightened/expanded
Extended-Move filter). Stage 5 (One-Candle Confirmation) is
confirm_follow_through() below, unchanged in spirit from the original
Early Breakout Engine -- still exactly one candle's wait, never more.
------------------------------------------------------------------------------
STATELESS DESIGN NOTE: nothing here persists a pending-breakout flag
across scanner runs. Instead, within any ONE run's candle window,
detect_early_breakout() searches BACKWARD up to
config.FRESH_BREAKOUT_MAX_AGE candles to find the breakout candle --
not just assuming it's always candles[-2] -- so a breakout is still
found even if a prior scan cycle missed it (a broker hiccup, a skipped
run, market-hours edge). The number of candles found between "now" and
the breakout candle IS its age, which the Fresh Breakout Score then
uses to discount stale detections rather than silently treating a
3-candle-old breakout exactly the same as one confirmed on the very
next candle.
"""

from config import (
    BREAKOUT_VOLUME_MULT, FOLLOW_THROUGH_MIN_VOLUME_PCT,
    EXTENDED_MOVE_MAX_PREMIUM_PCT, EXTENDED_MOVE_MAX_ATR_MULT,
    EXTENDED_MOVE_MAX_MOMENTUM_CANDLES, PARABOLIC_BODY_GROWTH_MULT,
    FRESH_BREAKOUT_SCORE, FRESH_BREAKOUT_MAX_AGE,
)
from indicators import compute_atr


def detect_early_breakout(candles: list, direction: str, vwap, ema9, ema21, ema50) -> dict:
    """
    Stage 4. Walks offsets 2..(FRESH_BREAKOUT_MAX_AGE + 2) from the end
    of `candles` -- offset=2 (candles[-2], "age" 0) is checked FIRST
    (freshest possible breakout), falling back to older offsets only if
    the freshest one isn't a genuine breakout. A candle only counts as
    THE breakout candle if the candle immediately before it did NOT
    already close beyond the same consolidation level -- this is what
    stops a continuation candle 2-3 candles into an already-running
    move from being mistaken for the first breakout candle.

    IMPORTANT: the consolidation window (base_window / consolidation_high
    / consolidation_low) is computed ONCE, anchored strictly before the
    OLDEST candidate age (not recomputed per-age from a shifting
    `candles[:-offset]` slice). An earlier version recomputed it per
    age, which could silently include already-broken-out candles in
    the "base" window for a small `age` when the true breakout happened
    a few candles further back -- that inflates consolidation_high
    with post-breakout price action, making a later CONTINUATION candle
    look like a fresh, valid breakout against an artificially raised
    bar. Anchoring the window once, before every candidate offset,
    removes that contamination path entirely (caught by
    test_early_breakout_engine.py's
    test_breakout_detector_finds_first_breakout_candle_not_a_later_one).
    """
    if len(candles) < 12:
        return {"breakout": False, "breakout_age": None, "fresh_score": 0, "reason": "insufficient candles"}

    oldest_offset = FRESH_BREAKOUT_MAX_AGE + 2
    if len(candles) < oldest_offset + 20:
        return {"breakout": False, "breakout_age": None, "fresh_score": 0, "reason": "insufficient candles"}

    base_window = candles[:-oldest_offset][-20:]
    if not base_window:
        return {"breakout": False, "breakout_age": None, "fresh_score": 0, "reason": "insufficient base window"}

    consolidation_high = max(c["high"] for c in base_window)
    consolidation_low = min(c["low"] for c in base_window)
    avg_volume = sum(c.get("volume") or 0 for c in base_window) / len(base_window)

    for age in range(0, FRESH_BREAKOUT_MAX_AGE + 1):
        offset = age + 2
        breakout_candle = candles[-offset]
        prior_candle = candles[-offset - 1]

        if direction == "CE":
            price_broke = breakout_candle["close"] > consolidation_high
            already_broken = prior_candle["close"] > consolidation_high
            vwap_ok = vwap is not None and breakout_candle["close"] > vwap
            ema_ok = None not in (ema9, ema21, ema50) and ema9 > ema21 > ema50
        else:
            price_broke = breakout_candle["close"] < consolidation_low
            already_broken = prior_candle["close"] < consolidation_low
            vwap_ok = vwap is not None and breakout_candle["close"] < vwap
            ema_ok = None not in (ema9, ema21, ema50) and ema9 < ema21 < ema50

        volume_ok = avg_volume > 0 and (breakout_candle.get("volume") or 0) > avg_volume * BREAKOUT_VOLUME_MULT

        if price_broke and not already_broken and volume_ok and vwap_ok and ema_ok:
            return {
                "breakout": True,
                "breakout_candle": breakout_candle,
                "breakout_age": age,
                "fresh_score": FRESH_BREAKOUT_SCORE.get(age, 0),
                "consolidation_high": consolidation_high,
                "consolidation_low": consolidation_low,
                "avg_volume": avg_volume,
                "detail": {"price_broke": True, "volume_ok": True, "vwap_ok": True, "ema_ok": True},
            }

    return {"breakout": False, "breakout_age": None, "fresh_score": 0, "detail": {}}


def confirm_follow_through(confirm_candle: dict, direction: str, breakout_candle: dict) -> dict:
    """
    Stage 5 -- One Candle Confirmation, ALWAYS exactly the single candle
    that closed right after `breakout_candle` (the caller works out
    which candle that is from detect_early_breakout()'s `breakout_age`
    -- see early_breakout_scanner.py). Bullish requires a Higher High
    AND Higher Low versus the breakout candle; bearish requires a Lower
    Low AND Lower High. Either way, volume must be at least
    FOLLOW_THROUGH_MIN_VOLUME_PCT of the breakout candle's.
    """
    breakout_volume = breakout_candle.get("volume") or 0
    volume_ok = breakout_volume > 0 and (confirm_candle.get("volume") or 0) >= breakout_volume * FOLLOW_THROUGH_MIN_VOLUME_PCT

    if direction == "CE":
        structure_ok = confirm_candle["high"] > breakout_candle["high"] and confirm_candle["low"] > breakout_candle["low"]
    else:
        structure_ok = confirm_candle["low"] < breakout_candle["low"] and confirm_candle["high"] < breakout_candle["high"]

    return {
        "confirmed": bool(structure_ok and volume_ok),
        "confirm_candle": confirm_candle,
        "detail": {"structure_ok": bool(structure_ok), "volume_ok": bool(volume_ok)},
    }


def _is_parabolic(candles: list, mult: float = PARABOLIC_BODY_GROWTH_MULT, streak: int = 3) -> bool:
    """True if each of the last `streak` candle bodies is >= `mult`x
    the one before it -- an accelerating, parabolic move rather than a
    steady trend."""
    if len(candles) < streak + 1:
        return False
    window = candles[-(streak + 1):]
    bodies = [abs(c["close"] - c["open"]) for c in window]
    if bodies[0] <= 0:
        return False
    return all(bodies[i + 1] >= bodies[i] * mult for i in range(len(bodies) - 1))


def extended_move_filter(candles: list, direction: str, breakout_premium, current_premium, ema20) -> dict:
    """
    Applied as a REJECTION check after Stage 5 confirms, before any
    alert fires. Rejects when ANY of:
      - option premium already moved more than
        EXTENDED_MOVE_MAX_PREMIUM_PCT (25%) since the breakout candle
      - underlying price is already more than EXTENDED_MOVE_MAX_ATR_MULT
        x ATR away from EMA20
      - more than EXTENDED_MOVE_MAX_MOMENTUM_CANDLES "momentum" candles
        (wider than 1x ATR) have printed in the recent window -- a
        proxy for "several large candles already printed", since this
        stateless design doesn't persist a literal since-breakout candle
        count across runs (see module docstring)
      - a parabolic (accelerating-body) move is already underway --
        added 2026-07-24, previously only checked via the premium/ATR
        legs above
    """
    atr = compute_atr(candles)
    reasons = []

    if breakout_premium and current_premium is not None:
        premium_move_pct = (current_premium - breakout_premium) / breakout_premium
        if abs(premium_move_pct) > EXTENDED_MOVE_MAX_PREMIUM_PCT:
            reasons.append(f"Premium already moved {premium_move_pct:.0%} since breakout")

    if atr and ema20 and candles:
        last_close = candles[-1]["close"]
        atr_distance = abs(last_close - ema20) / atr
        if atr_distance > EXTENDED_MOVE_MAX_ATR_MULT:
            reasons.append(f"Price is {atr_distance:.1f}x ATR from EMA20")

    if atr:
        recent = candles[-(EXTENDED_MOVE_MAX_MOMENTUM_CANDLES + 2):]
        large_candles = sum(1 for c in recent if (c["high"] - c["low"]) > atr)
        if large_candles > EXTENDED_MOVE_MAX_MOMENTUM_CANDLES:
            reasons.append(f"{large_candles} momentum candles already printed recently")

    if _is_parabolic(candles):
        reasons.append("Parabolic move already underway (accelerating candle bodies)")

    return {"extended": bool(reasons), "reasons": reasons}
