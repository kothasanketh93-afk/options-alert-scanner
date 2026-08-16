"""
app/engines/early_breakout/breakout_score.py -- Fix 11/12: the Early Breakout Engine's
own composite score, grade thresholds, and probability mapping.
------------------------------------------------------------------------------
Kept SEPARATE from trade_score.py's TRADE_SCORE_WEIGHTS / ENTRY_SCORE /
PROBABILITY_BANDS -- the two engines score fundamentally different
things (this one scores an early setup's quality; trade_score.py
scores a strike's current-state quality) and are tuned independently.
Nothing in trade_score.py is changed by this module; trade_score.
liquidity_strength() is imported and reused as-is (see
early_breakout_scanner.py) rather than duplicated.
"""

from config import (
    EARLY_BREAKOUT_WEIGHTS, EARLY_BREAKOUT_OI_BONUS, EARLY_BREAKOUT_GRADE_THRESHOLDS,
    EARLY_BREAKOUT_MIN_RR, FVG_CONFIRMATION_BONUS, PRICE_ACTION_CONFIRMATION_BONUS,
)
from config import OI_BUILD_THRESHOLD_PCT


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def trend_strength(ema9, ema21, ema50, direction: str) -> float:
    """
    Fix 5: EMA9/21/50 alignment. Binary-aligned-or-not floors at 0.5
    once aligned (so a barely-aligned trend still counts as trend-
    confirmed, per the spec's requirement, rather than being scored
    as weak), graded up to 1.0 by how cleanly separated the three
    EMAs are (a wider spread = a more established trend).
    """
    if None in (ema9, ema21, ema50):
        return 0.0
    aligned = (ema9 > ema21 > ema50) if direction == "CE" else (ema9 < ema21 < ema50)
    if not aligned:
        return 0.0
    spread_pct = abs(ema9 - ema50) / ema50 if ema50 else 0.0
    return _clamp01(0.5 + min(spread_pct / 0.01, 1.0) * 0.5)


def breakout_strength(breakout_result: dict) -> float:
    """Fraction of Stage 2's four sub-checks (price_broke, volume_ok,
    vwap_ok, ema_ok) that passed -- breakout_detector.detect_early_breakout()
    already requires ALL four to call it a breakout at all, so this is
    always 1.0 for anything reaching the scorer; kept as a fraction
    (not a hardcoded 1.0) so a future relaxation of that AND-gate
    still produces a meaningful graded score instead of a silent 1.0."""
    checks = breakout_result.get("detail", {})
    if not checks:
        return 0.0
    return sum(1 for v in checks.values() if v) / len(checks)


def volume_strength(breakout_candle_volume, avg_volume) -> float:
    """Full score at 2x the required BREAKOUT_VOLUME_MULT surge (i.e.
    3x the base-window average by default) -- a much bigger surge than
    the 1.5x minimum still only earns 1.0, it isn't uncapped."""
    if not avg_volume:
        return 0.0
    from config import BREAKOUT_VOLUME_MULT
    return _clamp01((breakout_candle_volume / avg_volume) / (BREAKOUT_VOLUME_MULT * 2))


def risk_reward_strength(rr_ratio: float) -> float:
    """Full score at 1.5x the minimum required R:R (EARLY_BREAKOUT_MIN_RR)."""
    return _clamp01(rr_ratio / (EARLY_BREAKOUT_MIN_RR * 1.5))


def follow_through_strength(follow_through_result: dict, confirm_volume, breakout_volume) -> float:
    """
    Stage 5's confirmation quality, not just its pass/fail gate (the
    gate itself -- confirmed True/False -- already happened in
    early_breakout_scanner.py before this is ever called). Graded by
    how much volume the confirmation candle carried relative to the
    breakout candle: right at the FOLLOW_THROUGH_MIN_VOLUME_PCT floor
    scores low, matching or exceeding the breakout candle's own volume
    scores full.
    """
    if not follow_through_result.get("confirmed"):
        return 0.0
    if not breakout_volume:
        return 0.5  # confirmed structurally but no volume baseline to grade against
    ratio = (confirm_volume or 0) / breakout_volume
    from config import FOLLOW_THROUGH_MIN_VOLUME_PCT
    return _clamp01((ratio - FOLLOW_THROUGH_MIN_VOLUME_PCT) / (1.0 - FOLLOW_THROUGH_MIN_VOLUME_PCT))


def institutional_strength(institutional_score: int) -> float:
    """app/engines/early_breakout/institutional_score.py's 0-100 score, rescaled to 0-1
    for the weighted composite below."""
    return _clamp01(institutional_score / 100)


def smart_money_strength(smart_money_score: int) -> float:
    """app/engines/early_breakout/smart_money_score.py's 0-100 score, rescaled to 0-1."""
    return _clamp01(smart_money_score / 100)


def classify_oi_state(oi_change_pct_value, price_moving_favorably: bool) -> str:
    """
    Fix 9: OI is a BONUS only, never a rejection reason for this
    engine. Simple, honestly-approximate classification (not a full
    futures-OI four-quadrant read, since this operates on the OPTION
    leg's own OI, not the underlying's futures OI):
      - OI rising AND price moving in the candidate's favor -> Long Buildup
      - OI falling AND price still moving in the candidate's favor -> Short Covering
      - anything else -> Neutral (0 bonus, never a penalty)
    """
    if oi_change_pct_value is None:
        return "NEUTRAL"
    if oi_change_pct_value >= OI_BUILD_THRESHOLD_PCT and price_moving_favorably:
        return "LONG_BUILDUP"
    if oi_change_pct_value <= -OI_BUILD_THRESHOLD_PCT and price_moving_favorably:
        return "SHORT_COVERING"
    return "NEUTRAL"


def compute_oi_bonus(oi_state: str) -> int:
    return EARLY_BREAKOUT_OI_BONUS.get(oi_state, 0)


def compute_fvg_bonus(fvg_confirmed: bool) -> int:
    """Fair Value Gap confirmation bonus (config.FVG_CONFIRMATION_BONUS,
    added 2026-07-25) -- same non-gating pattern as the OI bonus above:
    an unfilled FVG in the trade's direction adds confidence, its
    absence never rejects a setup that already cleared every stage."""
    return FVG_CONFIRMATION_BONUS if fvg_confirmed else 0


def compute_price_action_bonus(price_action_confirmed: bool) -> int:
    """Candlestick Price Action confirmation bonus (config.
    PRICE_ACTION_CONFIRMATION_BONUS, added 2026-07-26) -- same
    non-gating pattern: an aligned candlestick pattern (Hammer,
    Bullish/Bearish Engulfing, Momentum Candle, etc.) on the breakout
    or confirmation candle adds confidence, its absence never rejects."""
    return PRICE_ACTION_CONFIRMATION_BONUS if price_action_confirmed else 0


def compute_weighted_score(strengths: dict) -> int:
    """The 100-point weighted composite (config.EARLY_BREAKOUT_WEIGHTS:
    Underlying Trend 20 / Institutional 15 / Base 15 / Breakout 15 /
    Follow-through 10 / Volume 10 / Momentum 5 / Liquidity 5 /
    Risk-Reward 5 / Smart Money 5).

    BUGFIX (2026-08-15, P0 audit follow-up): `strengths.get(key, 0.0)`
    only supplies its default when `key` is genuinely ABSENT from the
    dict -- it does nothing when the key IS present but its value is
    None (e.g. trade_score.liquidity_strength() now correctly returns
    None for a missing/invalid quote -- see that function's own
    docstring). `_clamp01(None)` would raise, since every key this
    caller supplies IS always present (just sometimes None). Now
    explicitly excludes a None-valued factor from BOTH the weighted
    sum and the total weight, renormalizing over whatever's actually
    available -- same "None means insufficient evidence, not a zero
    score" principle trade_score.compute_trade_score() already applies
    (this module never reused that function directly, so it needed its
    own, parallel fix)."""
    total = 0.0
    available_weight = 0.0
    for key, weight in EARLY_BREAKOUT_WEIGHTS.items():
        value = strengths.get(key)
        if value is None:
            continue
        total += _clamp01(value) * weight
        available_weight += weight
    if available_weight <= 0:
        return 0
    return round(total * (sum(EARLY_BREAKOUT_WEIGHTS.values()) / available_weight))


def total_score(strengths: dict, oi_state: str, fresh_score: int = 100, fvg_confirmed: bool = False, price_action_confirmed: bool = False) -> int:
    """
    Weighted composite + OI bonus + FVG confirmation bonus + Price
    Action confirmation bonus, then the Fresh Breakout Score age decay
    (config.FRESH_BREAKOUT_SCORE, added 2026-07-24) applied as a
    MULTIPLIER on top -- a setup that's structurally perfect but 2-3
    candles stale should still score lower than an identical one caught
    on the very next candle, per the spec's "Breakout Candle 100 / Next
    Candle 90 / Second Candle 75 / Third Candle 40" table.
    `fresh_score` defaults to 100 (no discount) for any caller that
    doesn't have a breakout age to report. `fvg_confirmed` and
    `price_action_confirmed` default to False (no bonus) for any caller
    that hasn't checked for one.
    """
    bonus = compute_oi_bonus(oi_state) + compute_fvg_bonus(fvg_confirmed) + compute_price_action_bonus(price_action_confirmed)
    base = max(0, min(100, compute_weighted_score(strengths) + bonus))
    return round(base * (fresh_score / 100))


def compute_grade(score: int) -> str:
    """Fix 12 thresholds, highest-first, first match wins -- same
    walking convention as trade_score.compute_grade()."""
    for threshold, label in EARLY_BREAKOUT_GRADE_THRESHOLDS:
        if score >= threshold:
            return label
    return "Reject"


def compute_probability_pct(score: int) -> int:
    """
    Simple linear calibration, same honesty caveat as
    trade_score.compute_probability_pct(): NOT backtested against real
    outcomes, a reasonable starting mapping to tune once real
    early-breakout trade history exists. Maps score 60 (the Watchlist
    floor) -> ~55%, 100 -> ~95%.
    """
    return round(_clamp01((score - 60) / 40) * 40 + 55)
