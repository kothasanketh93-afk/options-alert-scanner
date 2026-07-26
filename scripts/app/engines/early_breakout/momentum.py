"""
app/engines/early_breakout/momentum.py -- combined momentum read for the Early Breakout
Engine (ADX, ROC, RSI slope, MACD histogram, candle body %, VWAP
distance, plus EMA slope and volume acceleration added in the
2026-07-24 Institutional Accumulation upgrade).
------------------------------------------------------------------------------
These indicators (RSI/MACD/ADX) are DELIBERATELY excluded from the core
V2.0 engine (see indicators.py's docstring) -- that decision still
stands for scanner.py/trade_score.py. This module is a separate,
explicit exception scoped only to the Early Breakout Engine, which
this spec (2026-07-24) specifically asked to use them for. Nothing in
indicators.py is changed.

All sub-scores are 0-1 and direction-aware (positive when the reading
favors the candidate's direction, 0 when it doesn't or data is
missing), same "clamp(x/ceiling, 0, 1)" convention as trade_score.py.
"""

from config import (
    MOMENTUM_ADX_CEILING, MOMENTUM_ROC_CEILING_PCT, MOMENTUM_RSI_SLOPE_CEILING,
    MOMENTUM_MACD_CEILING_PCT, MOMENTUM_BODY_CEILING_PCT, MOMENTUM_VWAP_CEILING_PCT,
    MOMENTUM_EMA_SLOPE_CEILING_PCT, MOMENTUM_VOLUME_ACCEL_CEILING,
)
from indicators import compute_ema


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def compute_rsi(closes: list, period: int = 14):
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def rsi_slope(closes: list, period: int = 14, lookback: int = 3):
    """Change in RSI over the last `lookback` closes -- needs the RSI
    recomputed at two points in time, not just the latest value."""
    if len(closes) < period + lookback + 1:
        return None
    rsi_now = compute_rsi(closes, period)
    rsi_then = compute_rsi(closes[:-lookback], period)
    if rsi_now is None or rsi_then is None:
        return None
    return rsi_now - rsi_then


def compute_roc(closes: list, period: int = 10):
    if len(closes) <= period:
        return None
    start = closes[-period - 1]
    if not start:
        return None
    return (closes[-1] - start) / start * 100


def compute_macd_histogram(closes: list, fast: int = 12, slow: int = 26, signal: int = 9):
    """
    MACD histogram = MACD line - its own `signal`-period EMA. Needs the
    MACD line's own history (not just its current value) to smooth
    into a signal line, so this rebuilds the MACD line candle-by-candle
    from `slow` onward -- more compute than the other indicators here,
    but only runs once per symbol+direction per scan cycle (Stage 2/3
    candidates only), not per strike.
    """
    if len(closes) < slow + signal:
        return None
    macd_line = []
    for i in range(slow, len(closes) + 1):
        window = closes[:i]
        ema_fast = compute_ema(window, fast)
        ema_slow = compute_ema(window, slow)
        if ema_fast is None or ema_slow is None:
            continue
        macd_line.append(ema_fast - ema_slow)
    if len(macd_line) < signal:
        return None
    signal_line = compute_ema(macd_line, signal)
    if signal_line is None:
        return None
    return macd_line[-1] - signal_line


def compute_adx(candles: list, period: int = 14):
    """Standard Wilder's ADX (+DI/-DI smoothed via Wilder's method, DX
    averaged over `period`)."""
    if len(candles) < period * 2:
        return None
    plus_dm, minus_dm, trs = [], [], []
    for i in range(1, len(candles)):
        up_move = candles[i]["high"] - candles[i - 1]["high"]
        down_move = candles[i - 1]["low"] - candles[i]["low"]
        plus_dm.append(up_move if (up_move > down_move and up_move > 0) else 0.0)
        minus_dm.append(down_move if (down_move > up_move and down_move > 0) else 0.0)
        tr = max(
            candles[i]["high"] - candles[i]["low"],
            abs(candles[i]["high"] - candles[i - 1]["close"]),
            abs(candles[i]["low"] - candles[i - 1]["close"]),
        )
        trs.append(tr)

    def _wilder_smooth(values):
        if len(values) < period:
            return None
        smoothed = [sum(values[:period])]
        for v in values[period:]:
            smoothed.append(smoothed[-1] - (smoothed[-1] / period) + v)
        return smoothed

    smoothed_tr = _wilder_smooth(trs)
    smoothed_plus = _wilder_smooth(plus_dm)
    smoothed_minus = _wilder_smooth(minus_dm)
    if not smoothed_tr or not smoothed_plus or not smoothed_minus:
        return None

    dx_values = []
    for tr, pdm, mdm in zip(smoothed_tr, smoothed_plus, smoothed_minus):
        if tr == 0:
            continue
        plus_di = 100 * pdm / tr
        minus_di = 100 * mdm / tr
        di_sum = plus_di + minus_di
        if di_sum == 0:
            continue
        dx_values.append(100 * abs(plus_di - minus_di) / di_sum)

    if len(dx_values) < period:
        return None
    return sum(dx_values[-period:]) / period


def ema_slope_pct(closes: list, period: int = 9, lookback: int = 3):
    """EMA's own change over the last `lookback` candles, as a %
    of price -- a smoothed alternative to raw price ROC, added in the
    2026-07-24 Institutional upgrade."""
    if len(closes) < period + lookback:
        return None
    ema_now = compute_ema(closes, period)
    ema_then = compute_ema(closes[:-lookback], period)
    if ema_now is None or ema_then is None or not ema_then:
        return None
    return (ema_now - ema_then) / ema_then


def volume_acceleration(candles: list, lookback: int = 5):
    """Recent-vs-prior volume ratio minus 1.0 -- e.g. 0.5 means the
    most recent `lookback` candles averaged 50% more volume than the
    `lookback` before that. Added in the 2026-07-24 Institutional
    upgrade; distinct from momentum's other volume-adjacent read
    (candle body %) and from base_detector's/institutional_score's own
    volume-trend checks, which use different lookbacks and purposes."""
    if len(candles) < lookback * 2:
        return None
    recent = candles[-lookback:]
    prior = candles[-lookback * 2:-lookback]
    recent_avg = sum(c.get("volume") or 0 for c in recent) / len(recent)
    prior_avg = sum(c.get("volume") or 0 for c in prior) / len(prior) if prior else 0.0
    if prior_avg <= 0:
        return None
    return (recent_avg / prior_avg) - 1.0


def candle_body_pct(candle: dict) -> float:
    rng = candle["high"] - candle["low"]
    if rng <= 0:
        return 0.0
    return abs(candle["close"] - candle["open"]) / rng


def momentum_strength(candles: list, direction: str, vwap, spot_price) -> dict:
    """
    Combined 0-1 momentum score = mean of eight direction-aware sub-
    scores (ADX is direction-agnostic -- it measures trend STRENGTH,
    not which way -- the other five are signed by `direction`).
    Missing/insufficient-data sub-factors contribute 0, same
    defensive convention as trade_score.py's strength functions.
    """
    closes = [c["close"] for c in candles]
    sign = 1 if direction == "CE" else -1

    adx = compute_adx(candles)
    roc = compute_roc(closes)
    rsi_sl = rsi_slope(closes)
    macd_hist = compute_macd_histogram(closes)
    body_pct = candle_body_pct(candles[-1]) if candles else 0.0
    last_close = closes[-1] if closes else None
    vwap_dist_pct = ((spot_price - vwap) / vwap) if (vwap and spot_price) else None
    macd_hist_pct = (macd_hist / last_close) if (macd_hist is not None and last_close) else None
    ema_slope = ema_slope_pct(closes)
    vol_accel = volume_acceleration(candles)

    adx_score = _clamp01((adx or 0.0) / MOMENTUM_ADX_CEILING)
    roc_score = _clamp01((sign * roc) / MOMENTUM_ROC_CEILING_PCT) if roc is not None else 0.0
    rsi_score = _clamp01((sign * rsi_sl) / MOMENTUM_RSI_SLOPE_CEILING) if rsi_sl is not None else 0.0
    macd_score = _clamp01((sign * macd_hist_pct) / MOMENTUM_MACD_CEILING_PCT) if macd_hist_pct is not None else 0.0
    body_score = _clamp01(body_pct / MOMENTUM_BODY_CEILING_PCT)
    vwap_score = _clamp01((sign * vwap_dist_pct) / MOMENTUM_VWAP_CEILING_PCT) if vwap_dist_pct is not None else 0.0
    ema_slope_score = _clamp01((sign * ema_slope) / MOMENTUM_EMA_SLOPE_CEILING_PCT) if ema_slope is not None else 0.0
    vol_accel_score = _clamp01(vol_accel / MOMENTUM_VOLUME_ACCEL_CEILING) if vol_accel is not None else 0.0

    combined = (
        adx_score + roc_score + rsi_score + macd_score + body_score + vwap_score
        + ema_slope_score + vol_accel_score
    ) / 8

    return {
        "score": combined,
        "detail": {
            "adx": round(adx, 1) if adx is not None else None,
            "roc_pct": round(roc, 2) if roc is not None else None,
            "rsi_slope": round(rsi_sl, 1) if rsi_sl is not None else None,
            "macd_hist_pct": round(macd_hist_pct, 4) if macd_hist_pct is not None else None,
            "body_pct": round(body_pct, 2),
            "vwap_dist_pct": round(vwap_dist_pct, 4) if vwap_dist_pct is not None else None,
            "ema_slope_pct": round(ema_slope, 4) if ema_slope is not None else None,
            "volume_acceleration": round(vol_accel, 2) if vol_accel is not None else None,
        },
    }
