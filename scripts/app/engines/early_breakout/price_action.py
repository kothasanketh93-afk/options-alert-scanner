"""
app/engines/early_breakout/price_action.py -- candlestick pattern
recognition, added 2026-07-26 as a scoped pilot slice of the requested
"Trading Intelligence Engine" (Part 4) / "Institutional Intelligence
Engine" (Part 3) -- both asked for this same list of candlestick
patterns, which is a signal this piece has real value, unlike the two
requests' other ~30 combined sub-engines (most already exist elsewhere
in this codebase, or need data/API calls this pass deliberately avoids
-- see engine.py's module docstring for the running list of what was
and wasn't built and why).

Reuses momentum.candle_body_pct() rather than duplicating it.

Every pattern here has a standard, textbook geometric definition (body
size relative to range, wick length relative to body, relationship to
the previous candle) -- picked for the same reason Fair Value Gap was:
low ambiguity, unlike chart patterns (Head & Shoulders, Cup & Handle,
Double Top/Bottom) which need multi-swing-point detection with much
more room to get subtly wrong. Chart patterns were NOT built in this
pass for that reason.
"""

from config import (
    DOJI_MAX_BODY_PCT, MARUBOZU_MIN_BODY_PCT, PIN_BAR_MIN_WICK_TO_BODY_RATIO,
    PIN_BAR_MAX_OPPOSITE_WICK_PCT, MOMENTUM_CANDLE_MIN_BODY_PCT, MOMENTUM_CANDLE_MIN_ATR_MULT,
)
from .momentum import candle_body_pct


def _wicks(candle: dict) -> tuple:
    """Returns (upper_wick, lower_wick, body_low, body_high)."""
    body_high = max(candle["open"], candle["close"])
    body_low = min(candle["open"], candle["close"])
    upper_wick = candle["high"] - body_high
    lower_wick = body_low - candle["low"]
    return upper_wick, lower_wick, body_low, body_high


def is_doji(candle: dict) -> bool:
    return candle_body_pct(candle) <= DOJI_MAX_BODY_PCT


def is_marubozu(candle: dict) -> bool:
    return candle_body_pct(candle) >= MARUBOZU_MIN_BODY_PCT


def is_hammer_or_shooting_star(candle: dict) -> str:
    """
    Returns "HAMMER" (long lower wick, small upper wick -- bullish
    reversal signal), "SHOOTING_STAR" (mirror, bearish), or None.
    Same geometric test as a "Pin Bar" -- textbook technical analysis
    treats these as the same shape, just named by context (a pin bar
    at a low = hammer, at a high = shooting star); this function
    doesn't know the surrounding trend, so it returns the neutral
    geometric read and the caller (price_action_confirmation() below)
    applies trade-direction context.
    """
    rng = candle["high"] - candle["low"]
    if rng <= 0:
        return None
    upper_wick, lower_wick, body_low, body_high = _wicks(candle)
    body = body_high - body_low

    if lower_wick >= body * PIN_BAR_MIN_WICK_TO_BODY_RATIO and upper_wick <= rng * PIN_BAR_MAX_OPPOSITE_WICK_PCT:
        return "HAMMER"
    if upper_wick >= body * PIN_BAR_MIN_WICK_TO_BODY_RATIO and lower_wick <= rng * PIN_BAR_MAX_OPPOSITE_WICK_PCT:
        return "SHOOTING_STAR"
    return None


def is_engulfing(candle: dict, prev_candle: dict) -> str:
    """Returns "BULLISH_ENGULFING", "BEARISH_ENGULFING", or None. Current
    candle's body must fully contain the previous candle's body AND be
    the opposite color."""
    _, _, prev_low, prev_high = _wicks(prev_candle)
    _, _, cur_low, cur_high = _wicks(candle)
    cur_bullish = candle["close"] > candle["open"]
    prev_bullish = prev_candle["close"] > prev_candle["open"]

    engulfs = cur_low <= prev_low and cur_high >= prev_high and (cur_high - cur_low) > (prev_high - prev_low)
    if not engulfs or cur_bullish == prev_bullish:
        return None
    return "BULLISH_ENGULFING" if cur_bullish else "BEARISH_ENGULFING"


def is_inside_or_outside_bar(candle: dict, prev_candle: dict) -> str:
    if candle["high"] <= prev_candle["high"] and candle["low"] >= prev_candle["low"]:
        return "INSIDE_BAR"
    if candle["high"] >= prev_candle["high"] and candle["low"] <= prev_candle["low"]:
        return "OUTSIDE_BAR"
    return None


def is_momentum_candle(candle: dict, atr: float) -> bool:
    """Large body (per MOMENTUM_CANDLE_MIN_BODY_PCT) on a range that's
    also meaningfully wide relative to ATR -- distinguishes a genuine
    momentum candle from a small-but-100%-body doji-less candle in a
    quiet, low-ATR market."""
    if not atr or atr <= 0:
        return False
    rng = candle["high"] - candle["low"]
    return candle_body_pct(candle) >= MOMENTUM_CANDLE_MIN_BODY_PCT and rng >= atr * MOMENTUM_CANDLE_MIN_ATR_MULT


def detect_candle_patterns(candle: dict, prev_candle: dict, atr: float) -> list:
    """
    Returns every pattern `candle` matches (not mutually exclusive --
    e.g. a candle can be both a Marubozu and a Momentum Candle). Each
    entry: {"pattern": str, "bullish": bool | None}. `bullish=None`
    for direction-neutral patterns (Doji, Inside/Outside Bar).
    """
    patterns = []

    if is_doji(candle):
        patterns.append({"pattern": "DOJI", "bullish": None})

    if is_marubozu(candle):
        patterns.append({"pattern": "MARUBOZU", "bullish": candle["close"] > candle["open"]})

    pin = is_hammer_or_shooting_star(candle)
    if pin == "HAMMER":
        patterns.append({"pattern": "HAMMER", "bullish": True})
    elif pin == "SHOOTING_STAR":
        patterns.append({"pattern": "SHOOTING_STAR", "bullish": False})

    if prev_candle is not None:
        engulf = is_engulfing(candle, prev_candle)
        if engulf == "BULLISH_ENGULFING":
            patterns.append({"pattern": "BULLISH_ENGULFING", "bullish": True})
        elif engulf == "BEARISH_ENGULFING":
            patterns.append({"pattern": "BEARISH_ENGULFING", "bullish": False})

        bar = is_inside_or_outside_bar(candle, prev_candle)
        if bar:
            patterns.append({"pattern": bar, "bullish": None})

    if is_momentum_candle(candle, atr):
        patterns.append({"pattern": "MOMENTUM_CANDLE", "bullish": candle["close"] > candle["open"]})

    return patterns


def price_action_confirmation(candles: list, direction: str, atr: float) -> dict:
    """
    Checks the breakout candle (candles[-2]) and confirmation candle
    (candles[-1]) for any pattern aligned with `direction` (bullish
    patterns for CE, bearish for PE) -- direction-neutral patterns
    (Doji, Inside/Outside Bar) never count as confirmation either way.

    Returns {"confirmed": bool, "patterns": [str, ...]} -- "patterns"
    lists every aligned match found across both candles, for the
    Telegram reasons text.
    """
    if len(candles) < 3:
        return {"confirmed": False, "patterns": []}

    wants_bullish = direction == "CE"
    matches = []
    for i in (-1, -2):
        candle = candles[i]
        prev_candle = candles[i - 1]
        for p in detect_candle_patterns(candle, prev_candle, atr):
            if p["bullish"] == wants_bullish:
                matches.append(p["pattern"])

    return {"confirmed": bool(matches), "patterns": matches}
