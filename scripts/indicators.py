"""
indicators.py -- Pure-math indicator helpers, split out of scanner.py.
-----------------------------------------------------------------------
No broker/network calls here -- everything takes candles/values in and
returns numbers or None. Deterministic arithmetic only, per the V2.0
spec (no RSI/MACD/Bollinger/SuperTrend/Stochastic/Ichimoku/CCI/ML).
"""

from config import (
    EFFICIENCY_TRENDING_THRESHOLD, ATR_VOLATILE_PCT_THRESHOLD, REGIME_SCORE_PENALTY,
)


def compute_ema(closes: list, period: int):
    if len(closes) < period:
        return None
    k = 2 / (period + 1)
    ema = sum(closes[:period]) / period
    for price in closes[period:]:
        ema = price * k + ema * (1 - k)
    return ema


def ema_trend_direction(candles: list) -> str:
    closes = [c["close"] for c in candles]
    ema9 = compute_ema(closes, 9)
    ema21 = compute_ema(closes, 21)
    if ema9 is None or ema21 is None:
        return None
    if ema9 > ema21:
        return "CE"
    if ema9 < ema21:
        return "PE"
    return None


def compute_atr(candles: list, period: int = 14):
    if len(candles) < period + 1:
        return None
    true_ranges = []
    for i in range(1, len(candles)):
        h, l = candles[i]["high"], candles[i]["low"]
        prev_close = candles[i - 1]["close"]
        tr = max(h - l, abs(h - prev_close), abs(l - prev_close))
        true_ranges.append(tr)
    recent = true_ranges[-period:]
    return sum(recent) / len(recent)


def compute_vwap(candles: list):
    total_pv = 0.0
    total_v = 0.0
    for c in candles:
        vol = c.get("volume")
        if vol is None:
            return None
        typical_price = (c["high"] + c["low"] + c["close"]) / 3
        total_pv += typical_price * vol
        total_v += vol
    if total_v == 0:
        return None
    return total_pv / total_v


def vwap_confirms(spot_price: float, vwap, direction: str) -> bool:
    """Mandatory VWAP filter: CE requires price > VWAP, PE requires price < VWAP."""
    if vwap is None:
        return False  # no data -> fail closed, don't guess
    if direction == "CE":
        return spot_price > vwap
    return spot_price < vwap


def efficiency_ratio(candles: list):
    """
    Kaufman-style efficiency ratio, 0-1: net move / sum of absolute
    candle-to-candle moves -- 1.0 is a perfectly clean trend, near 0.0
    is pure chop.

    BUGFIX (2026-08-07): used to return 0.0 for both "genuinely no net
    trend" (enough candles, but they cancel out) AND "not enough
    candles to compute a ratio at all" (<2 closes) -- collapsing
    "unknown" into "confirmed worst case", the exact same class of bug
    trade_score.compute_trade_score()'s 2026-07-30 EMA fix already
    addressed for that one factor. Returns None now when there aren't
    enough candles; callers (trade_score.trend_strength(),
    scanner._broad_market_efficiency_ratio()) must treat None as
    "unavailable, exclude from the weighted average", not 0.0. A
    genuine total_move == 0 with >=2 candles is still a real answer
    (price literally round-tripped to its start) and still returns
    0.0, not None -- that distinction is intentional, not a leftover.
    """
    closes = [c["close"] for c in candles]
    if len(closes) < 2:
        return None
    net_move = abs(closes[-1] - closes[0])
    total_move = sum(abs(closes[i] - closes[i - 1]) for i in range(1, len(closes)))
    if total_move == 0:
        return 0.0
    return net_move / total_move


def market_regime(candles_15m: list) -> tuple:
    er = efficiency_ratio(candles_15m)
    atr = compute_atr(candles_15m)
    last_close = candles_15m[-1]["close"] if candles_15m else None

    # BUGFIX (2026-08-07): efficiency_ratio() can now return None
    # (not enough candles -- see its own docstring) -- `er >=
    # EFFICIENCY_TRENDING_THRESHOLD` would raise TypeError on None
    # where it used to silently compare 0.0 >= threshold (always
    # False, falling through to the ATR/SIDEWAYS checks below). Same
    # fall-through behavior, explicit instead of accidental.
    if er is not None and er >= EFFICIENCY_TRENDING_THRESHOLD:
        return "TRENDING", REGIME_SCORE_PENALTY["TRENDING"]

    if atr is not None and last_close:
        atr_pct = atr / last_close
        if atr_pct >= ATR_VOLATILE_PCT_THRESHOLD:
            return "VOLATILE", REGIME_SCORE_PENALTY["VOLATILE"]

    return "SIDEWAYS", REGIME_SCORE_PENALTY["SIDEWAYS"]


def momentum_pct(candles: list, lookback: int = 6):
    """
    Phase 3 (trade_score.py): signed price momentum over the last
    `lookback` candles, as a fraction of the starting price -- e.g.
    lookback=6 on 15-min candles is roughly the last 1.5 hours. Not
    direction-aware itself (caller decides whether a positive or
    negative reading favors CE vs. PE) -- same "pure math in, caller
    applies direction" convention as ema_trend_direction() above.
    Returns None if there aren't enough candles rather than guessing.
    """
    if len(candles) <= lookback:
        return None
    start_close = candles[-lookback - 1]["close"]
    end_close = candles[-1]["close"]
    if not start_close:
        return None
    return (end_close - start_close) / start_close


def find_nearby_level(candles: list, direction: str, lookback: int = 20):
    window = candles[-lookback:]
    if not window:
        return None
    if direction == "CE":
        return max(c["high"] for c in window)
    return min(c["low"] for c in window)
