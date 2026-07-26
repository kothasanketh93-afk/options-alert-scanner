"""
app/engines/early_breakout/institutional_score.py -- Stage 2: Institutional Accumulation.
------------------------------------------------------------------------------
Computed entirely on the UNDERLYING's own candles, deliberately BEFORE
any option/strike is selected -- this keeps Stage 1's "the option is
selected ONLY AFTER the underlying confirms" principle intact. Two of
the spec's requested sub-factors ("Increasing Open Interest", "Large
Bid Quantity") are option-contract-level or futures-level signals that
literally can't be read before a strike/expiry has been picked --
this module honestly does NOT fabricate them here. They're measured
where they actually can be, once the ATM leg is resolved: see
smart_money_score.py's "oi_buildup" and "bid_dominance" (bid-ask
SPREAD tightness -- a proxy, since Angel One/Upstox's option-chain
responses don't include order-book QUANTITY, only best bid/ask
PRICE -- see the caveat in smart_money_score.py's docstring).

Six sub-factors ARE readable at the underlying level and are averaged
equally, same convention as base_detector.py:
  - Increasing volume (recent vs. prior window)
  - Decreasing volatility / ATR compression
  - Narrow candle bodies (small body-to-range ratio, on average)
  - Repeated higher lows OR repeated lower highs (whichever side is
    stronger -- reuses base_detector's pivot counters)
  - Repeated VWAP respect (fraction of recent candles that closed
    within a small tolerance of VWAP, or on the same side of it
    consistently)
  - Compression before expansion (ATR trending DOWN over the lookback,
    not just currently low -- catches a base that's actively tightening,
    not one that's already been tight and flat for a long time)
"""

from config import BASE_HIGHER_LOWS_MIN, BASE_LOWER_HIGHS_MIN
from .base_detector import higher_lows_count, lower_highs_count


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))



def _volume_increase_score(candles: list, lookback: int = 10) -> float:
    if len(candles) < lookback * 2:
        return 0.0
    recent = candles[-lookback:]
    prior = candles[-lookback * 2:-lookback]
    recent_avg = sum(c.get("volume") or 0 for c in recent) / len(recent)
    prior_avg = sum(c.get("volume") or 0 for c in prior) / len(prior) if prior else 0.0
    if prior_avg <= 0:
        return 0.0
    # Full score at a sustained 20%+ increase; 0 at flat/declining.
    return _clamp01((recent_avg / prior_avg - 1.0) / 0.2)


def _volatility_decrease_score(candles: list, lookback: int = 10) -> float:
    """ATR-style true-range average, most-recent window vs. the window
    before it -- same contraction shape as base_detector._contraction_score(),
    duplicated here (not imported) since this one is intentionally
    scoped to the Institutional stage's own lookback, kept independently
    tunable."""
    if len(candles) < lookback * 2 + 1:
        return 0.0
    def _avg_tr(window, prev_close):
        trs = []
        pc = prev_close
        for c in window:
            trs.append(max(c["high"] - c["low"], abs(c["high"] - pc), abs(c["low"] - pc)))
            pc = c["close"]
        return sum(trs) / len(trs)

    prior = candles[-lookback * 2:-lookback]
    recent = candles[-lookback:]
    prior_avg = _avg_tr(prior, candles[-lookback * 2 - 1]["close"])
    recent_avg = _avg_tr(recent, prior[-1]["close"])
    if prior_avg <= 0:
        return 0.0
    ratio = recent_avg / prior_avg
    return _clamp01((1.0 - ratio) / 0.4)


def _narrow_body_score(candles: list, lookback: int = 10) -> float:
    """Average body-to-range ratio over the lookback -- full score at
    <=30% (small, indecisive candles typical of quiet accumulation),
    zero at >=70% (large, directional candles)."""
    window = candles[-lookback:]
    ratios = []
    for c in window:
        rng = c["high"] - c["low"]
        if rng > 0:
            ratios.append(abs(c["close"] - c["open"]) / rng)
    if not ratios:
        return 0.0
    avg_body = sum(ratios) / len(ratios)
    return _clamp01((0.7 - avg_body) / 0.4)


def _vwap_respect_score(candles: list, vwap, lookback: int = 10) -> float:
    """Fraction of the lookback window's candles that closed on the
    SAME side of VWAP as the most recent candle -- consistent respect
    of one side (not necessarily proximity) is the accumulation
    signal; a market chopping across VWAP scores low."""
    if vwap is None:
        return 0.0
    window = candles[-lookback:]
    if not window:
        return 0.0
    latest_side = window[-1]["close"] >= vwap
    same_side = sum(1 for c in window if (c["close"] >= vwap) == latest_side)
    return _clamp01(same_side / len(window))


def _compression_trend_score(candles: list, lookback: int = 10) -> float:
    """
    Compression BEFORE expansion, not just low volatility right now:
    splits the lookback into three equal thirds and checks that each
    third's average true range is smaller than the one before it
    (oldest -> newest strictly decreasing) -- an ACTIVELY tightening
    base, not a base that's been flat all along (which
    _volatility_decrease_score() above would also reward, but doesn't
    distinguish "still tightening" from "already fully quiet").
    """
    if len(candles) < lookback:
        return 0.0
    window = candles[-lookback:]
    third = max(2, len(window) // 3)
    thirds = [window[:third], window[third:2 * third], window[2 * third:]]
    thirds = [t for t in thirds if t]
    if len(thirds) < 3:
        return 0.0
    avgs = [sum(c["high"] - c["low"] for c in t) / len(t) for t in thirds]
    if avgs[0] <= 0:
        return 0.0
    # full score if each third is materially tighter than the last
    decreasing_steps = sum(1 for a, b in zip(avgs, avgs[1:]) if b < a * 0.9)
    return _clamp01(decreasing_steps / 2)


def compute_institutional_score(candles: list, vwap) -> dict:
    """
    Stage 2 entry point. `candles` is the same base window
    base_detector.compute_base_score() receives (i.e. excluding the
    breakout/confirmation candles -- see early_breakout_scanner.py).

    Returns {"institutional_score": 0-100 int, "detail": {...}}.
    """
    volume_score = _volume_increase_score(candles)
    volatility_score = _volatility_decrease_score(candles)
    body_score = _narrow_body_score(candles)
    vwap_score = _vwap_respect_score(candles, vwap)
    compression_score = _compression_trend_score(candles)

    structure_window = candles[-20:]
    higher_lows = higher_lows_count(structure_window)
    lower_highs = lower_highs_count(structure_window)
    structure_score = max(
        _clamp01(higher_lows / BASE_HIGHER_LOWS_MIN),
        _clamp01(lower_highs / BASE_LOWER_HIGHS_MIN),
    )

    institutional_score = round(
        100 * (volume_score + volatility_score + body_score + vwap_score + compression_score + structure_score) / 6
    )

    return {
        "institutional_score": institutional_score,
        "detail": {
            "volume_increase": round(volume_score, 2),
            "volatility_decrease": round(volatility_score, 2),
            "narrow_bodies": round(body_score, 2),
            "vwap_respect": round(vwap_score, 2),
            "compression_trend": round(compression_score, 2),
            "structure": round(structure_score, 2),
        },
    }
