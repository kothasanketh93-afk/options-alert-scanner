"""
app/engines/early_breakout/base_detector.py -- Stage 1: Base Formation Detector.
------------------------------------------------------------------------------
Pure-math, no broker calls -- same convention as indicators.py. Takes
candles/spot/vwap/atr already fetched by early_breakout_scanner.py and
returns a 0-100 base_score plus the sub-factor breakdown, so a symbol
only proceeds to Stage 2 (breakout_detector.py) once a real
consolidation is actually in place.

Five equal-weighted sub-factors (per the spec's bullet list), plus a
sixth added in the 2026-07-24 upgrade (chart-pattern bonus, see
compute_pattern_bonus() below):
  - ATR / candle-range contraction
  - Swing structure (>=3 Higher Lows for a bullish base, >=3 Lower
    Highs for a bearish one)
  - Volume trend (slowly increasing, not spiking -- a spike belongs to
    Stage 2's breakout, not the base itself)
  - Price proximity to VWAP
  - Consolidation width relative to ATR (a tight range, not a wide
    chop)
  - Chart-pattern bonus (Triangle/Pennant/Rectangle/Flag geometry,
    failed breakouts/breakdowns, volume dry-up)
These are averaged, not weighted against each other, because Fix 11's
100-point composite score (breakout_score.py) already gives "Base" a
single fixed weight slot -- a second layer of weights here would be
redundant tuning surface for no real benefit.
"""

from config import BASE_LOOKBACK_CANDLES, BASE_HIGHER_LOWS_MIN, BASE_LOWER_HIGHS_MIN


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _contraction_score(candles: list, lookback: int = 10) -> float:
    """
    1.0 when the average candle range over the most recent `lookback`
    candles has contracted to <=60% of the range over the `lookback`
    candles before that (strong ATR/range contraction); 0.0 when it's
    flat or still expanding. Linear in between.
    """
    if len(candles) < lookback * 2:
        return 0.0
    recent = candles[-lookback:]
    prior = candles[-lookback * 2:-lookback]
    recent_avg = sum(c["high"] - c["low"] for c in recent) / len(recent)
    prior_avg = sum(c["high"] - c["low"] for c in prior) / len(prior) if prior else 0.0
    if prior_avg <= 0:
        return 0.0
    ratio = recent_avg / prior_avg
    return _clamp01((1.0 - ratio) / 0.4)


def _find_pivot_lows(candles: list) -> list:
    """Simple 1-candle-either-side pivot low detector -- a deterministic
    swing-structure read, not a full zig-zag/fractal indicator."""
    return [
        candles[i]["low"] for i in range(1, len(candles) - 1)
        if candles[i]["low"] < candles[i - 1]["low"] and candles[i]["low"] < candles[i + 1]["low"]
    ]


def _find_pivot_highs(candles: list) -> list:
    return [
        candles[i]["high"] for i in range(1, len(candles) - 1)
        if candles[i]["high"] > candles[i - 1]["high"] and candles[i]["high"] > candles[i + 1]["high"]
    ]


def higher_lows_count(candles: list) -> int:
    """Counts consecutive higher-low STEPS, read backwards from the most
    recent pivot so a currently-forming structure is picked up even if
    earlier pivots in the window were choppier."""
    pivots = _find_pivot_lows(candles)
    if len(pivots) < 2:
        return 0
    steps = 0
    for i in range(len(pivots) - 1, 0, -1):
        if pivots[i] > pivots[i - 1]:
            steps += 1
        else:
            break
    return steps


def lower_highs_count(candles: list) -> int:
    pivots = _find_pivot_highs(candles)
    if len(pivots) < 2:
        return 0
    steps = 0
    for i in range(len(pivots) - 1, 0, -1):
        if pivots[i] < pivots[i - 1]:
            steps += 1
        else:
            break
    return steps


def _volume_trend_score(candles: list, lookback: int = 10) -> float:
    """
    Rewards a GENTLE volume increase (a base slowly attracting
    interest), not a spike (that's Stage 2's job). Ratio <=1.0 (flat/
    declining) scores proportionally low; 1.0-1.3x scores full; beyond
    1.3x falls back off -- volume rising that fast usually means the
    breakout has already started, so it no longer looks like "still
    basing".
    """
    if len(candles) < lookback * 2:
        return 0.0
    recent = candles[-lookback:]
    prior = candles[-lookback * 2:-lookback]
    recent_avg = sum(c.get("volume") or 0 for c in recent) / len(recent)
    prior_avg = sum(c.get("volume") or 0 for c in prior) / len(prior) if prior else 0.0
    if prior_avg <= 0:
        return 0.0
    ratio = recent_avg / prior_avg
    if ratio <= 1.0:
        return _clamp01(ratio)
    if ratio <= 1.3:
        return 1.0
    return _clamp01(1.0 - (ratio - 1.3) / 0.7)


def _vwap_proximity_score(spot_price: float, vwap, atr) -> float:
    if vwap is None or not atr:
        return 0.0
    distance = abs(spot_price - vwap)
    return _clamp01(1.0 - min(distance / atr, 1.0))


def _consolidation_width_score(candles: list, atr, lookback: int = 15) -> float:
    """Full score when the lookback window's high-low range is <=2x
    ATR (a tight base); zero once it's >=5x ATR (too wide to call a
    base)."""
    if not atr or atr <= 0:
        return 0.0
    window = candles[-lookback:]
    if not window:
        return 0.0
    width = max(c["high"] for c in window) - min(c["low"] for c in window)
    return _clamp01((5.0 * atr - width) / (3.0 * atr))


def compute_pattern_bonus(candles: list, lookback: int = 20) -> dict:
    """
    Stage 3 (upgrade, 2026-07-24): a simplified GEOMETRIC pattern read,
    not a trained/ML pattern classifier -- deliberately conservative
    and explainable, consistent with the rest of this codebase's
    "simple, honestly-approximate" scoring conventions:
      - Pivot highs falling AND pivot lows rising -> "Triangle/Pennant"
      - Pivot highs AND lows both roughly flat -> "Rectangle/Box Range"
      - Pivot highs AND lows both trending the SAME direction, at
        roughly the same slope (a parallel channel) -> "Flag"
      - Only one side flat -> "Partial Range" (half credit)
    Plus, independent of which shape (if any) was found:
      - Failed breakouts/breakdowns: the window's back 30% poking
        beyond the range the front 70% established, then closing back
        inside it -- classic "shakeout" behaviour before a real move
      - Volume dry-up: the most recent 5 candles' average volume well
        below the whole window's average

    Returns {"pattern_score": 0-1, "pattern_label": str|None, "detail": {...}}.
    `pattern_score` is meant to be averaged into compute_base_score()'s
    other five sub-factors as a sixth, same equal-weighting convention.
    """
    window = candles[-lookback:]
    if len(window) < 10:
        return {"pattern_score": 0.0, "pattern_label": None, "detail": {}}

    highs = _find_pivot_highs(window)
    lows = _find_pivot_lows(window)

    pattern_label = None
    pattern_score = 0.0
    if len(highs) >= 2 and len(lows) >= 2:
        avg_price = sum(c["close"] for c in window) / len(window)
        tol = avg_price * 0.003  # ~0.3% flatness tolerance, scaled to price level

        high_slope = highs[-1] - highs[0]
        low_slope = lows[-1] - lows[0]
        flat_top = (max(highs) - min(highs)) <= tol * len(highs)
        flat_bottom = (max(lows) - min(lows)) <= tol * len(lows)
        converging = high_slope < -tol and low_slope > tol
        parallel = (
            abs(high_slope - low_slope) <= tol * 2
            and ((high_slope > tol and low_slope > tol) or (high_slope < -tol and low_slope < -tol))
        )

        if flat_top and flat_bottom:
            pattern_label, pattern_score = "Rectangle / Box Range", 1.0
        elif converging:
            pattern_label, pattern_score = "Triangle / Pennant", 1.0
        elif parallel:
            pattern_label, pattern_score = "Flag", 0.8
        elif flat_top or flat_bottom:
            pattern_label, pattern_score = "Partial Range", 0.5

    split = max(1, int(len(window) * 0.7))
    early, late = window[:split], window[split:]
    failed_breakouts = failed_breakdowns = 0
    if early and late:
        range_high = max(c["high"] for c in early)
        range_low = min(c["low"] for c in early)
        failed_breakouts = sum(1 for c in late if c["high"] > range_high and c["close"] <= range_high)
        failed_breakdowns = sum(1 for c in late if c["low"] < range_low and c["close"] >= range_low)
    failed_score = _clamp01((failed_breakouts + failed_breakdowns) / 2)

    base_vol = sum(c.get("volume") or 0 for c in window) / len(window)
    recent_tail = window[-5:]
    recent_vol = sum(c.get("volume") or 0 for c in recent_tail) / len(recent_tail)
    dryup_score = _clamp01(1.0 - (recent_vol / base_vol)) if base_vol else 0.0

    combined = (pattern_score + failed_score + dryup_score) / 3
    return {
        "pattern_score": combined,
        "pattern_label": pattern_label,
        "detail": {
            "failed_breakouts": failed_breakouts,
            "failed_breakdowns": failed_breakdowns,
            "volume_dryup": round(dryup_score, 2),
        },
    }


def compute_base_score(candles: list, spot_price: float, vwap, atr) -> dict:
    """
    Stage 1 entry point. `candles` should already EXCLUDE the breakout/
    confirmation candles the caller is about to test in Stage 2/3 (see
    early_breakout_scanner.py -- it passes candles[:-2]), so the base
    score reflects the consolidation BEFORE the move being evaluated,
    not the move itself.

    Returns:
      base_score:      0-100 int
      direction_bias:  "CE" | "PE" | None -- which side's swing
                       structure is stronger, purely informational;
                       breakout_detector.py independently decides the
                       actual breakout direction from price action and
                       does not need to agree with this.
      higher_lows / lower_highs: raw pivot-step counts, for logging
      detail: the five sub-scores, for SCORE-style log lines
    """
    contraction = _contraction_score(candles)
    volume_trend = _volume_trend_score(candles)
    vwap_score = _vwap_proximity_score(spot_price, vwap, atr)
    width_score = _consolidation_width_score(candles, atr)

    structure_window = candles[-BASE_LOOKBACK_CANDLES:]
    higher_lows = higher_lows_count(structure_window)
    lower_highs = lower_highs_count(structure_window)
    structure_bullish = _clamp01(higher_lows / BASE_HIGHER_LOWS_MIN)
    structure_bearish = _clamp01(lower_highs / BASE_LOWER_HIGHS_MIN)
    structure_score = max(structure_bullish, structure_bearish)

    pattern = compute_pattern_bonus(candles, lookback=BASE_LOOKBACK_CANDLES)

    base_score = round(
        100 * (contraction + volume_trend + vwap_score + width_score + structure_score
               + pattern["pattern_score"]) / 6
    )

    direction_bias = None
    if structure_bullish > structure_bearish and higher_lows >= BASE_HIGHER_LOWS_MIN:
        direction_bias = "CE"
    elif structure_bearish > structure_bullish and lower_highs >= BASE_LOWER_HIGHS_MIN:
        direction_bias = "PE"

    return {
        "base_score": base_score,
        "direction_bias": direction_bias,
        "higher_lows": higher_lows,
        "lower_highs": lower_highs,
        "pattern_label": pattern["pattern_label"],
        "detail": {
            "contraction": round(contraction, 2),
            "volume_trend": round(volume_trend, 2),
            "vwap_proximity": round(vwap_score, 2),
            "width": round(width_score, 2),
            "structure": round(structure_score, 2),
            "pattern": round(pattern["pattern_score"], 2),
            "pattern_label": pattern["pattern_label"],
            "failed_breakouts": pattern["detail"].get("failed_breakouts"),
            "failed_breakdowns": pattern["detail"].get("failed_breakdowns"),
            "volume_dryup": pattern["detail"].get("volume_dryup"),
        },
    }
