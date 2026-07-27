"""
probability/scorer.py -- Turns raw Signal fields into 0-1 factor strengths.

Each `score_*` function is defensive: missing input -> 0.0 contribution
(never raises, never guesses a value the signal didn't provide -- this
mirrors the host scanner's "fail closed" convention seen in
indicators.vwap_confirms()).
"""

from __future__ import annotations

from typing import Optional

from ..models.signal import Signal


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def score_trend(signal: Signal) -> float:
    if signal.trend is None or signal.direction is None:
        return 0.0
    wants_up = signal.direction.upper() == "CE"
    if signal.trend.upper() == "UP":
        return 1.0 if wants_up else 0.0
    if signal.trend.upper() == "DOWN":
        return 1.0 if not wants_up else 0.0
    return 0.3  # FLAT/unknown trend -> weak, not zero (some setups are mean-reversion)


def score_ema(signal: Signal) -> float:
    if signal.ema_fast is None or signal.ema_slow is None or not signal.spot_price:
        return 0.0
    spread_pct = abs(signal.ema_fast - signal.ema_slow) / signal.spot_price
    aligned = (
        signal.ema_fast > signal.ema_slow
        if signal.direction and signal.direction.upper() == "CE"
        else signal.ema_fast < signal.ema_slow
    )
    if not aligned:
        return 0.0
    return _clamp01(spread_pct / 0.01)  # 1% spread => full strength


def score_vwap(signal: Signal) -> float:
    if signal.vwap is None or signal.spot_price is None:
        return 0.0
    is_ce = bool(signal.direction and signal.direction.upper() == "CE")
    confirms = signal.spot_price > signal.vwap if is_ce else signal.spot_price < signal.vwap
    if not confirms:
        return 0.0
    distance_pct = abs(signal.spot_price - signal.vwap) / signal.spot_price
    return _clamp01(distance_pct / 0.006)


def score_rsi(signal: Signal) -> float:
    if signal.rsi is None:
        return 0.0
    is_ce = bool(signal.direction and signal.direction.upper() == "CE")
    # Momentum-confirming zones rather than classic overbought/oversold reversal.
    if is_ce:
        return _clamp01((signal.rsi - 50) / 25)
    return _clamp01((50 - signal.rsi) / 25)


def score_adx(signal: Signal) -> float:
    if signal.adx is None:
        return 0.0
    return _clamp01(signal.adx / 40)


def score_atr(signal: Signal) -> float:
    if signal.atr is None or not signal.spot_price:
        return 0.0
    atr_pct = signal.atr / signal.spot_price
    # Some volatility is healthy (participation); extreme volatility is not
    # rewarded further past ~1.5% ATR/price.
    return _clamp01(atr_pct / 0.015)


def score_pcr(signal: Signal) -> float:
    if signal.pcr is None:
        return 0.0
    is_ce = bool(signal.direction and signal.direction.upper() == "CE")
    # High PCR (more puts) is traditionally bullish contrarian; low PCR bearish.
    if is_ce:
        return _clamp01((signal.pcr - 1.0) / 0.6)
    return _clamp01((1.0 - signal.pcr) / 0.6) if signal.pcr < 1.0 else 0.0


def score_oi(signal: Signal) -> float:
    if signal.oi_change_pct is None:
        return 0.0
    return _clamp01(abs(signal.oi_change_pct) / 25.0)


def score_volume(signal: Signal) -> float:
    if signal.volume is None:
        return 0.0
    if signal.avg_volume:
        ratio = signal.volume / signal.avg_volume
        return _clamp01((ratio - 1.0) / 1.0)  # 2x avg volume => full strength
    if signal.min_volume_threshold:
        return _clamp01(signal.volume / (signal.min_volume_threshold * 2))
    return 0.0


def score_iv(signal: Signal) -> float:
    if signal.iv is None:
        return 0.0
    # Moderate IV (15-35) scores best; very low or very high IV scores lower.
    if signal.iv < 15:
        return _clamp01(signal.iv / 15)
    if signal.iv <= 35:
        return 1.0
    return _clamp01(1.0 - (signal.iv - 35) / 40)


def score_momentum(signal: Signal) -> float:
    if signal.momentum_pct is None:
        return 0.0
    is_ce = bool(signal.direction and signal.direction.upper() == "CE")
    signed = signal.momentum_pct if is_ce else -signal.momentum_pct
    return _clamp01(signed / 0.8)


def score_market_breadth(signal: Signal) -> float:
    if signal.market_breadth is None:
        return 0.0
    is_ce = bool(signal.direction and signal.direction.upper() == "CE")
    signed = signal.market_breadth if is_ce else -signal.market_breadth
    return _clamp01((signed + 1) / 2) if signed >= -1 else 0.0


def score_breakout_strength(signal: Signal) -> float:
    if signal.breakout_strength is None:
        return 0.0
    return _clamp01(signal.breakout_strength)


def score_candle_structure(signal: Signal) -> float:
    if not signal.candle_structure:
        return 0.0
    is_ce = bool(signal.direction and signal.direction.upper() == "CE")
    bullish = {"bullish_engulfing", "hammer", "morning_star", "marubozu_bullish"}
    bearish = {"bearish_engulfing", "shooting_star", "evening_star", "marubozu_bearish"}
    key = signal.candle_structure.lower()
    if is_ce:
        return 1.0 if key in bullish else (0.0 if key in bearish else 0.3)
    return 1.0 if key in bearish else (0.0 if key in bullish else 0.3)


FACTOR_SCORERS = {
    "trend": score_trend,
    "ema": score_ema,
    "vwap": score_vwap,
    "rsi": score_rsi,
    "adx": score_adx,
    "atr": score_atr,
    "pcr": score_pcr,
    "oi": score_oi,
    "volume": score_volume,
    "iv": score_iv,
    "momentum": score_momentum,
    "market_breadth": score_market_breadth,
    "breakout_strength": score_breakout_strength,
    "candle_structure": score_candle_structure,
}
