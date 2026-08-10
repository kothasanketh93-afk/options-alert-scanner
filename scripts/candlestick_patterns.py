"""
candlestick_patterns.py -- Pure-math candlestick pattern detection.
------------------------------------------------------------------------------
Same convention as indicators.py: no broker/network calls, no pandas/
numpy/ta-lib -- deterministic arithmetic on the SAME candle lists
(list of {"open","high","low","close",...} dicts, ascending oldest ->
newest) that scanner.py already holds in memory each cycle. Nothing
here fetches a candle, calls a broker, or recomputes anything another
module already produced.

13 patterns, single-candle through 3-candle:
  Doji, Hammer, Shooting Star, Spinning Top, Marubozu (1-candle)
  Bullish/Bearish Engulfing, Inside Bar, Outside Bar (2-candle)
  Morning Star, Evening Star, Three White Soldiers, Three Black Crows (3-candle)

Every detector looks ONLY at the most recent formation ending at
candles[-1] -- this is "did a pattern just form", not a historical
scan back through the whole list.

DESIGN NOTE (advisory, not a gate): detect()/pattern_read() below never
reject a trade themselves. analyze() returns a continuous 0-1
"alignment score" for the caller's chosen direction, meant to be fed
into trade_score.py as one more continuous strength (the same
convention every other Trade Score factor already follows) or read by
the Decision Engine as a small, BOUNDED confidence nudge -- see
app/engines/decision_engine/candlestick_adapter.py. This mirrors the
lesson already learned and documented in option_chain.directional_bias()
about NOT hard-gating on a single secondary signal.
"""

from config import (
    CANDLESTICK_DOJI_BODY_MAX_RATIO, CANDLESTICK_SMALL_BODY_MAX_RATIO,
    CANDLESTICK_LONG_WICK_MIN_MULT, CANDLESTICK_MARUBOZU_WICK_MAX_RATIO,
    CANDLESTICK_SOLDIERS_MIN_BODY_RATIO,
    CANDLESTICK_TREND_CONTEXT_LOOKBACK, CANDLESTICK_MIN_RANGE_VS_AVG_RATIO,
    CANDLESTICK_RANGE_AVG_LOOKBACK, CANDLESTICK_VOLUME_CONFIRM_MULT,
    CANDLESTICK_VOLUME_CONFIRM_BONUS,
)

# ---------------------------------------------------------------------------
# Shared candle geometry helpers
# ---------------------------------------------------------------------------

def _body(c: dict) -> float:
    return abs(c["close"] - c["open"])


def _range(c: dict) -> float:
    return c["high"] - c["low"]


def _upper_wick(c: dict) -> float:
    return c["high"] - max(c["open"], c["close"])


def _lower_wick(c: dict) -> float:
    return min(c["open"], c["close"]) - c["low"]


def _is_bullish(c: dict) -> bool:
    return c["close"] > c["open"]


def _is_bearish(c: dict) -> bool:
    return c["close"] < c["open"]


def _body_ratio(c: dict):
    """body / range, or None if this candle has zero range (open==high==low==close,
    e.g. a stale/illiquid print) -- callers must treat None as no-read, not zero."""
    rng = _range(c)
    if rng <= 0:
        return None
    return _body(c) / rng


# ---------------------------------------------------------------------------
# Accuracy pass (2026-08-10): trend-context and range-significance helpers.
# Both stay self-contained (pure math on the same candle list, no import
# from indicators.py) -- matching this module's own "no broker/network
# calls, deterministic arithmetic only" design, and no dependency on
# whatever else scanner.py has already computed this cycle.
# ---------------------------------------------------------------------------

def _preceding_trend(candles: list, lookback: int = None):
    """
    'up'/'down'/None -- net close-to-close direction over `lookback`
    candles immediately BEFORE the most recent one. The most recent
    candle (candles[-1]) is EXCLUDED from this read -- it's the
    pattern candle a reversal-type pattern (Hammer/Shooting Star) is
    being read on, so it must not contaminate "what was the trend
    INTO this candle" (comparing it against itself would trivially
    bias the read). None if there aren't enough prior candles, or if
    the net move over the window is exactly flat (open to ambiguity,
    not a claimed direction).
    """
    lookback = lookback or CANDLESTICK_TREND_CONTEXT_LOOKBACK
    if len(candles) < lookback + 2:
        return None
    start_close = candles[-(lookback + 2)]["close"]
    end_close = candles[-2]["close"]
    if start_close == end_close:
        return None
    return "up" if end_close > start_close else "down"


def _recent_avg_range(candles: list, lookback: int = None, exclude_last: int = 1):
    """
    Simple average high-low range over `lookback` candles BEFORE the
    most recent `exclude_last` candle(s) -- a lightweight volatility
    reference so a pattern's own range can be judged against what's
    typical right now, without importing indicators.compute_atr()
    (kept self-contained, see module docstring). None if there aren't
    enough prior candles.
    """
    lookback = lookback or CANDLESTICK_RANGE_AVG_LOOKBACK
    window = candles[-(lookback + exclude_last):-exclude_last] if exclude_last else candles[-lookback:]
    if not window:
        return None
    return sum(_range(c) for c in window) / len(window)


def _range_significant(candles: list, candle: dict) -> bool:
    """
    True if `candle`'s own range is at least
    CANDLESTICK_MIN_RANGE_VS_AVG_RATIO of the recent average range --
    filters out statistically-insignificant micro-candles that happen
    to match a pattern's shape ratios but carry little real
    directional weight (e.g. a "Marubozu" whose absolute range is far
    below today's normal volatility). If there isn't enough history to
    compute a recent average yet, this does NOT block the pattern --
    same "insufficient data is not confirmed-bad" posture the rest of
    this codebase already follows (see indicators.efficiency_ratio()'s
    own docstring) -- it only filters when there's actually a baseline
    to compare against.
    """
    avg_range = _recent_avg_range(candles)
    if avg_range is None or avg_range <= 0:
        return True  # no baseline yet -- don't block on absence of data
    return _range(candle) >= avg_range * CANDLESTICK_MIN_RANGE_VS_AVG_RATIO


def _volume_confirm_bonus(candles: list, candle: dict) -> float:
    """
    0 or CANDLESTICK_VOLUME_CONFIRM_BONUS -- a small, bounded strength
    bonus when `candle`'s own volume is at/above
    CANDLESTICK_VOLUME_CONFIRM_MULT x the recent average volume (real
    participation behind the move, not just the candle's shape).
    Returns 0 (no penalty, no bonus) whenever volume data is missing
    on any relevant candle -- soft confirmation only, never a
    requirement, matching this module's advisory-only design.
    """
    if candle.get("volume") is None:
        return 0.0
    window = candles[-(CANDLESTICK_RANGE_AVG_LOOKBACK + 1):-1]
    volumes = [c.get("volume") for c in window if c.get("volume") is not None]
    if not volumes:
        return 0.0
    avg_volume = sum(volumes) / len(volumes)
    if avg_volume <= 0:
        return 0.0
    if candle["volume"] >= avg_volume * CANDLESTICK_VOLUME_CONFIRM_MULT:
        return CANDLESTICK_VOLUME_CONFIRM_BONUS
    return 0.0


# ---------------------------------------------------------------------------
# 1-candle patterns
# ---------------------------------------------------------------------------

def is_doji(candles: list) -> bool:
    if not candles:
        return False
    ratio = _body_ratio(candles[-1])
    return ratio is not None and ratio <= CANDLESTICK_DOJI_BODY_MAX_RATIO


def is_spinning_top(candles: list) -> bool:
    """Small body strictly between the Doji floor and the small-body
    ceiling, with BOTH wicks at least as long as the body -- indecision
    with real (if balanced) two-sided rejection, unlike a Doji's
    near-zero body."""
    if not candles:
        return False
    c = candles[-1]
    ratio = _body_ratio(c)
    if ratio is None or not (CANDLESTICK_DOJI_BODY_MAX_RATIO < ratio <= CANDLESTICK_SMALL_BODY_MAX_RATIO):
        return False
    body = _body(c)
    return _upper_wick(c) >= body and _lower_wick(c) >= body


def marubozu_direction(candles: list):
    """Returns 'bullish', 'bearish', or None. Full-body candle: both
    wicks negligible relative to range -- a strong, uninterrupted
    directional close.

    ACCURACY (2026-08-10): now also requires the candle's range to be
    "significant" against recent average range (see
    _range_significant()) -- a full-body candle whose absolute range
    is tiny compared to today's normal volatility isn't a strong move,
    just a quiet candle with clean geometry.
    """
    if not candles:
        return None
    c = candles[-1]
    rng = _range(c)
    if rng <= 0:
        return None
    if (_upper_wick(c) / rng) > CANDLESTICK_MARUBOZU_WICK_MAX_RATIO:
        return None
    if (_lower_wick(c) / rng) > CANDLESTICK_MARUBOZU_WICK_MAX_RATIO:
        return None
    if not _range_significant(candles, c):
        return None
    if _is_bullish(c):
        return "bullish"
    if _is_bearish(c):
        return "bearish"
    return None  # open == close with negligible wicks is really a Doji, not a Marubozu


def is_hammer(candles: list) -> bool:
    """Small body near the TOP of the range, long lower wick, small/no
    upper wick. Bullish-reversal shape -- caller supplies trend context
    (this function only reads the candle's own geometry, same
    "pure math in, caller applies direction/context" convention as
    indicators.ema_trend_direction()).

    ACCURACY (2026-08-10): also requires range significance (see
    _range_significant()) -- preceding-trend context (the OTHER half
    of a real Hammer read: this shape after an uptrend is a Hanging
    Man, not a Hammer) is applied by the caller (detect(), below),
    not here, since this function's contract is geometry only.
    """
    if not candles:
        return False
    c = candles[-1]
    ratio = _body_ratio(c)
    if ratio is None or ratio > CANDLESTICK_SMALL_BODY_MAX_RATIO:
        return False
    body = _body(c)
    if body == 0:
        return False  # zero-body is Doji territory, not Hammer
    lower = _lower_wick(c)
    upper = _upper_wick(c)
    if not (lower >= CANDLESTICK_LONG_WICK_MIN_MULT * body and upper <= body):
        return False
    return _range_significant(candles, c)


def is_shooting_star(candles: list) -> bool:
    """Mirror of is_hammer(): small body near the BOTTOM of the range,
    long upper wick, small/no lower wick. Bearish-reversal shape.
    See is_hammer()'s docstring for the range-significance and
    trend-context notes -- identical reasoning, mirrored."""
    if not candles:
        return False
    c = candles[-1]
    ratio = _body_ratio(c)
    if ratio is None or ratio > CANDLESTICK_SMALL_BODY_MAX_RATIO:
        return False
    body = _body(c)
    if body == 0:
        return False
    upper = _upper_wick(c)
    lower = _lower_wick(c)
    if not (upper >= CANDLESTICK_LONG_WICK_MIN_MULT * body and lower <= body):
        return False
    return _range_significant(candles, c)


# ---------------------------------------------------------------------------
# 2-candle patterns
# ---------------------------------------------------------------------------

def is_bullish_engulfing(candles: list) -> bool:
    if len(candles) < 2:
        return False
    c1, c2 = candles[-2], candles[-1]
    # ACCURACY (2026-08-10): c1 must itself have a real body -- without
    # this, a near-zero-body c1 (a Doji-like candle) gets trivially
    # "engulfed" by almost any c2, producing spurious Engulfing reads
    # on quiet/choppy days that aren't a genuine reversal of anything.
    c1_ratio = _body_ratio(c1)
    if c1_ratio is None or c1_ratio <= CANDLESTICK_DOJI_BODY_MAX_RATIO:
        return False
    if not (
        _is_bearish(c1) and _is_bullish(c2)
        and c2["open"] <= c1["close"] and c2["close"] >= c1["open"]
        and _body(c2) > _body(c1)
    ):
        return False
    return _range_significant(candles, c2)


def is_bearish_engulfing(candles: list) -> bool:
    if len(candles) < 2:
        return False
    c1, c2 = candles[-2], candles[-1]
    c1_ratio = _body_ratio(c1)
    if c1_ratio is None or c1_ratio <= CANDLESTICK_DOJI_BODY_MAX_RATIO:
        return False
    if not (
        _is_bullish(c1) and _is_bearish(c2)
        and c2["open"] >= c1["close"] and c2["close"] <= c1["open"]
        and _body(c2) > _body(c1)
    ):
        return False
    return _range_significant(candles, c2)


def is_inside_bar(candles: list) -> bool:
    """Current candle's full range sits inside the prior candle's range
    -- consolidation/indecision, direction-neutral."""
    if len(candles) < 2:
        return False
    c1, c2 = candles[-2], candles[-1]
    return c2["high"] <= c1["high"] and c2["low"] >= c1["low"]


def is_outside_bar(candles: list) -> bool:
    """Current candle's range fully contains the prior candle's range
    (range-based -- NOT the same test as engulfing, which is body-based).
    Volatility expansion; direction read from the outside candle's own
    close vs. open."""
    if len(candles) < 2:
        return False
    c1, c2 = candles[-2], candles[-1]
    return c2["high"] >= c1["high"] and c2["low"] <= c1["low"]


# ---------------------------------------------------------------------------
# 3-candle patterns
# ---------------------------------------------------------------------------

def is_morning_star(candles: list) -> bool:
    """Sizable bearish candle, then a small-bodied middle candle, then
    a sizable bullish candle closing back above the first candle's
    midpoint -- classic bottoming reversal."""
    if len(candles) < 3:
        return False
    c1, c2, c3 = candles[-3], candles[-2], candles[-1]
    r1, r3 = _body_ratio(c1), _body_ratio(c3)
    if r1 is None or r3 is None:
        return False
    if not (_is_bearish(c1) and r1 >= CANDLESTICK_SMALL_BODY_MAX_RATIO):
        return False
    if not (_is_bullish(c3) and r3 >= CANDLESTICK_SMALL_BODY_MAX_RATIO):
        return False
    r2 = _body_ratio(c2)
    if r2 is not None and r2 > CANDLESTICK_SMALL_BODY_MAX_RATIO:
        return False  # middle candle isn't small-bodied -- not a real "star"
    c1_mid = (c1["open"] + c1["close"]) / 2
    return c3["close"] >= c1_mid


def is_evening_star(candles: list) -> bool:
    """Mirror of is_morning_star(): sizable bullish, small-bodied
    middle, sizable bearish closing back below the first candle's
    midpoint -- classic topping reversal."""
    if len(candles) < 3:
        return False
    c1, c2, c3 = candles[-3], candles[-2], candles[-1]
    r1, r3 = _body_ratio(c1), _body_ratio(c3)
    if r1 is None or r3 is None:
        return False
    if not (_is_bullish(c1) and r1 >= CANDLESTICK_SMALL_BODY_MAX_RATIO):
        return False
    if not (_is_bearish(c3) and r3 >= CANDLESTICK_SMALL_BODY_MAX_RATIO):
        return False
    r2 = _body_ratio(c2)
    if r2 is not None and r2 > CANDLESTICK_SMALL_BODY_MAX_RATIO:
        return False
    c1_mid = (c1["open"] + c1["close"]) / 2
    return c3["close"] <= c1_mid


def is_three_white_soldiers(candles: list) -> bool:
    """Three consecutive sizable-bodied bullish candles, each closing
    higher than the last, each opening inside the previous candle's
    body -- steady, uninterrupted buying."""
    if len(candles) < 3:
        return False
    c1, c2, c3 = candles[-3], candles[-2], candles[-1]
    for c in (c1, c2, c3):
        ratio = _body_ratio(c)
        if ratio is None or not _is_bullish(c) or ratio < CANDLESTICK_SOLDIERS_MIN_BODY_RATIO:
            return False
    rising_closes = c1["close"] < c2["close"] < c3["close"]
    opens_within_prior_body = (c1["open"] <= c2["open"] <= c1["close"]) and \
                               (c2["open"] <= c3["open"] <= c2["close"])
    return rising_closes and opens_within_prior_body


def is_three_black_crows(candles: list) -> bool:
    """Mirror of is_three_white_soldiers(): three consecutive
    sizable-bodied bearish candles, each closing lower, each opening
    inside the previous candle's body -- steady, uninterrupted selling."""
    if len(candles) < 3:
        return False
    c1, c2, c3 = candles[-3], candles[-2], candles[-1]
    for c in (c1, c2, c3):
        ratio = _body_ratio(c)
        if ratio is None or not _is_bearish(c) or ratio < CANDLESTICK_SOLDIERS_MIN_BODY_RATIO:
            return False
    falling_closes = c1["close"] > c2["close"] > c3["close"]
    opens_within_prior_body = (c1["close"] <= c2["open"] <= c1["open"]) and \
                               (c2["close"] <= c3["open"] <= c2["open"])
    return falling_closes and opens_within_prior_body


# ---------------------------------------------------------------------------
# Aggregation: detect() + direction-aware scoring
# ---------------------------------------------------------------------------

# (pattern key, detector fn, bias, base_strength 0-1 used by
# pattern_alignment_score() below). Only the 3-candle patterns still use
# this table-driven form -- Doji/Spinning Top/Hammer/Shooting Star/
# Marubozu/Engulfing/Inside Bar/Outside Bar are now handled inline in
# detect() below (2026-08-10 accuracy pass: several of them need
# per-read context -- preceding trend, range significance, volume
# confirmation -- that a fixed (name, fn, bias, strength) tuple can't
# express).
_THREE_CANDLE_PATTERNS = (
    ("Morning Star", is_morning_star, "bullish", 0.85),
    ("Evening Star", is_evening_star, "bearish", 0.85),
    ("Three White Soldiers", is_three_white_soldiers, "bullish", 0.85),
    ("Three Black Crows", is_three_black_crows, "bearish", 0.85),
)


def detect(candles: list) -> list:
    """
    Every pattern that matches the MOST RECENT formation in `candles`
    (ascending oldest -> newest). Returns a list of
    {"pattern": str, "bias": "bullish"|"bearish"|"neutral", "strength": float}
    dicts -- zero, one, or several (patterns aren't mutually exclusive;
    e.g. a Marubozu close can also complete an Engulfing pattern).
    Empty list if there aren't enough candles or nothing matched --
    never raises.

    ACCURACY (2026-08-10): Hammer/Shooting Star are reversal-type
    patterns that classically only mean what they're scored as in the
    RIGHT preceding-trend context (a Hammer after a downtrend is
    bullish reversal; the identical shape after an uptrend is a
    Hanging Man -- bearish exhaustion, not implemented here, see
    module docstring). When the preceding trend (see
    _preceding_trend()) CONTRADICTS the pattern's expected reversal
    context, its bias is downgraded to "neutral" here rather than
    confidently scored the wrong direction -- still reported in
    patterns_detected/detail for visibility, just excluded from
    pattern_alignment_score()'s directional average (which only
    counts bullish/bearish entries). Unknown preceding trend (None,
    not enough history) does NOT downgrade -- absence of context isn't
    evidence of the WRONG context.
    """
    if not candles:
        return []

    found = []

    if is_doji(candles):
        found.append({"pattern": "Doji", "bias": "neutral", "strength": 0.30})
    if is_spinning_top(candles):
        found.append({"pattern": "Spinning Top", "bias": "neutral", "strength": 0.25})

    if is_hammer(candles):
        bias = "neutral" if _preceding_trend(candles) == "up" else "bullish"
        strength = 0.70 + (_volume_confirm_bonus(candles, candles[-1]) if bias != "neutral" else 0.0)
        found.append({"pattern": "Hammer", "bias": bias, "strength": min(1.0, strength)})

    if is_shooting_star(candles):
        bias = "neutral" if _preceding_trend(candles) == "down" else "bearish"
        strength = 0.70 + (_volume_confirm_bonus(candles, candles[-1]) if bias != "neutral" else 0.0)
        found.append({"pattern": "Shooting Star", "bias": bias, "strength": min(1.0, strength)})

    marubozu_bias = marubozu_direction(candles)
    if marubozu_bias:
        strength = 0.60 + _volume_confirm_bonus(candles, candles[-1])
        found.append({"pattern": "Marubozu", "bias": marubozu_bias, "strength": min(1.0, strength)})

    if len(candles) >= 2:
        if is_bullish_engulfing(candles):
            strength = 0.90 + _volume_confirm_bonus(candles, candles[-1])
            found.append({"pattern": "Bullish Engulfing", "bias": "bullish", "strength": min(1.0, strength)})
        if is_bearish_engulfing(candles):
            strength = 0.90 + _volume_confirm_bonus(candles, candles[-1])
            found.append({"pattern": "Bearish Engulfing", "bias": "bearish", "strength": min(1.0, strength)})
        if is_inside_bar(candles):
            found.append({"pattern": "Inside Bar", "bias": "neutral", "strength": 0.20})
        if is_outside_bar(candles) and _range_significant(candles, candles[-1]):
            outside_bias = "bullish" if _is_bullish(candles[-1]) else "bearish"
            found.append({"pattern": "Outside Bar", "bias": outside_bias, "strength": 0.50})

    if len(candles) >= 3:
        for name, fn, bias, strength in _THREE_CANDLE_PATTERNS:
            if fn(candles):
                found.append({"pattern": name, "bias": bias, "strength": strength})

    return found


def pattern_alignment_score(candles: list, direction: str):
    """
    0-1 continuous read of how much the most recently formed
    candlestick pattern(s) support `direction` ("CE" or "PE") --
    same "continuous 0-1 strength" convention every trade_score.py
    factor already follows, so this can be dropped straight into that
    weighted-average model. Only DIRECTIONAL patterns (bullish/bearish
    bias) count toward the average; neutral patterns (Doji, Spinning
    Top, Inside Bar) are excluded from the score itself -- they're
    still returned by detect() for display/logging, since "the market
    is indecisive right now" is useful context even though it shouldn't
    mathematically push the score toward either direction.

    Returns None if no directional pattern was found this cycle --
    callers should treat None as "no read" (exclude from any weighted
    average), NOT as 0.5/neutral, matching indicators.py's existing
    "None means unavailable, not neutral" convention for missing data.
    """
    detected = detect(candles)
    directional = [d for d in detected if d["bias"] in ("bullish", "bearish")]
    if not directional:
        return None

    total = 0.0
    for d in directional:
        matches = (d["bias"] == "bullish" and direction == "CE") or \
                  (d["bias"] == "bearish" and direction == "PE")
        total += d["strength"] if matches else (1.0 - d["strength"])
    return total / len(directional)


def dominant_pattern(candles: list):
    """The single highest-strength pattern detected this cycle (for
    display/Telegram/logging) -- {"pattern","bias","strength"} or None."""
    detected = detect(candles)
    if not detected:
        return None
    return max(detected, key=lambda d: d["strength"])


def analyze(candles: list, direction: str) -> dict:
    """
    Standard Output Object for this module -- same "additive dict
    attached to the trade" convention as options_intelligence.analyze().
    Always returns a dict (never raises, never returns None) so callers
    can attach it unconditionally.
    """
    detected = detect(candles)
    score = pattern_alignment_score(candles, direction)
    top = dominant_pattern(candles)
    return {
        "enabled": True,
        "patterns_detected": [d["pattern"] for d in detected],
        "detail": detected,
        "dominant_pattern": top["pattern"] if top else None,
        "dominant_bias": top["bias"] if top else None,
        "alignment_score": score,  # 0-1 or None -- see pattern_alignment_score() docstring
        "alignment_score_pct": round(score * 100) if score is not None else None,
    }
