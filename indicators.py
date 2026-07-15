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


def efficiency_ratio(candles: list) -> float:
    closes = [c["close"] for c in candles]
    if len(closes) < 2:
        return 0.0
    net_move = abs(closes[-1] - closes[0])
    total_move = sum(abs(closes[i] - closes[i - 1]) for i in range(1, len(closes)))
    if total_move == 0:
        return 0.0
    return net_move / total_move


def market_regime(candles_15m: list) -> tuple:
    er = efficiency_ratio(candles_15m)
    atr = compute_atr(candles_15m)
    last_close = candles_15m[-1]["close"] if candles_15m else None

    if er >= EFFICIENCY_TRENDING_THRESHOLD:
        return "TRENDING", REGIME_SCORE_PENALTY["TRENDING"]

    if atr is not None and last_close:
        atr_pct = atr / last_close
        if atr_pct >= ATR_VOLATILE_PCT_THRESHOLD:
            return "VOLATILE", REGIME_SCORE_PENALTY["VOLATILE"]

    return "SIDEWAYS", REGIME_SCORE_PENALTY["SIDEWAYS"]


def find_nearby_level(candles: list, direction: str, lookback: int = 20):
    window = candles[-lookback:]
    if not window:
        return None
    if direction == "CE":
        return max(c["high"] for c in window)
    return min(c["low"] for c in window)
