"""
trade_score.py -- Phase 3: Professional Trade Score Engine.
------------------------------------------------------------------------------
Replaces grading.py's binary strengths (1.0/0.0 per factor -> weighted
0-100) with the phase-3 spec's continuous weighted model: Trend 20,
Volume 15, OI 15, Momentum 10, VWAP 10, EMA 10, Risk Reward 5,
Liquidity 5, Market Strength 5, HTF 5 (config.TRADE_SCORE_WEIGHTS).

grading.py is NOT deleted -- nothing outside scanner.py's active path
imports it, so it's left in place as a reference/fallback, but
scanner.score_strike() now calls this module instead.

WHY RSI/PCR AREN'T HERE: the phase-3 spec assumes both are already
calculated. Neither is, in this codebase (RSI is deliberately excluded
per the earlier V2.0 spec; PCR was never implemented) -- per an
explicit decision, this engine is built entirely from indicators
already computed elsewhere rather than bolting on two new ones.
"Market Strength" is filled by a broad-index (NIFTY) efficiency-ratio
read instead -- a reasonable stand-in for "is the overall tape moving
with conviction right now", built from the same efficiency_ratio()
math already used for this symbol's own Trend factor.

TELEGRAM DISPLAY: per an explicit decision, this REVERSES the earlier
V2.0 "no raw numeric score ever reaches Telegram" design -- Trade
Score, Probability, Confidence, Risk, Reward, and Expected R:R are now
all shown, matching the phase-3 spec's example output. See
telegram_notify.py's fmt_new_alert().
"""

from config import (
    TRADE_SCORE_WEIGHTS, TRADE_SCORE_GRADE_THRESHOLDS, CONFIDENCE_STARS_BY_TRADE_GRADE,
    TS_EMA_SPREAD_CEILING_PCT, TS_MOMENTUM_CEILING_PCT, TS_VWAP_DIST_CEILING_PCT,
    TS_OI_BUILD_CEILING_PCT, TS_VOLUME_CEILING_MULT, TS_LIQUIDITY_CEILING_MULT,
    TS_RR_BASELINE, MIN_OI, MIN_OPTION_VOLUME, MAX_SPREAD_PCT,
    STOCK_SYMBOLS, STOCK_MIN_OI, STOCK_MIN_OPTION_VOLUME,
    ATR_VOLATILE_PCT_THRESHOLD, EXPIRY_DAY_THRESHOLD_DAYS,
    PROBABILITY_BANDS,
)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


# ---------------------------------------------------------------------------
# Continuous 0-1 strength factors. Each takes raw inputs scanner.py already
# gathers per strike (or per symbol) and normalizes against a config ceiling.
# ---------------------------------------------------------------------------

def trend_strength(efficiency_ratio_value: float) -> float:
    """efficiency_ratio() from indicators.py is already 0-1 -- this
    IS the trend-cleanliness read, no further scaling needed."""
    return _clamp01(efficiency_ratio_value or 0.0)


def ema_strength(ema9: float, ema21: float, price: float) -> float:
    if not price:
        return 0.0
    spread_pct = abs(ema9 - ema21) / price
    return _clamp01(spread_pct / TS_EMA_SPREAD_CEILING_PCT)


def volume_strength(option_volume: float, symbol: str = None) -> float:
    min_volume = STOCK_MIN_OPTION_VOLUME if symbol in STOCK_SYMBOLS else MIN_OPTION_VOLUME
    if not min_volume:
        return 0.0
    return _clamp01(option_volume / (min_volume * TS_VOLUME_CEILING_MULT))


def oi_build_strength(oi_change_pct: float) -> float:
    return _clamp01(abs(oi_change_pct or 0.0) / TS_OI_BUILD_CEILING_PCT)


def momentum_strength(signed_momentum_pct) -> float:
    if signed_momentum_pct is None:
        return 0.0
    return _clamp01(signed_momentum_pct / TS_MOMENTUM_CEILING_PCT)


def vwap_strength(signed_distance_pct) -> float:
    if signed_distance_pct is None:
        return 0.0
    return _clamp01(signed_distance_pct / TS_VWAP_DIST_CEILING_PCT)


def risk_reward_strength(target_distance: float, sl_distance: float) -> float:
    if not sl_distance:
        return 0.0
    rr_ratio = target_distance / sl_distance
    return _clamp01(rr_ratio / TS_RR_BASELINE)


def liquidity_strength(oi: float, volume: float, bid: float, ask: float,
                        premium: float, symbol: str = None) -> float:
    """
    Averages three sub-ratios (OI vs. min, volume vs. min, and spread
    tightness) -- same "strength, not just pass/fail" idea
    grading.py's original docstring described for this factor but
    never implemented (it was folded into a single binary
    liquidity_pass before). This is a SEPARATE, softer read from
    filters.liquidity_ok()'s hard gate -- a strike can pass the hard
    gate by a hair (liquidity_strength close to 0) or clear it
    comfortably (liquidity_strength close to 1).
    """
    is_stock = symbol in STOCK_SYMBOLS
    min_oi = STOCK_MIN_OI if is_stock else MIN_OI
    min_volume = STOCK_MIN_OPTION_VOLUME if is_stock else MIN_OPTION_VOLUME

    oi_ratio = _clamp01(oi / (min_oi * TS_LIQUIDITY_CEILING_MULT)) if min_oi else 0.0
    vol_ratio = _clamp01(volume / (min_volume * TS_LIQUIDITY_CEILING_MULT)) if min_volume else 0.0

    if premium:
        spread_pct = (ask - bid) / premium
        spread_ratio = _clamp01(1.0 - (spread_pct / MAX_SPREAD_PCT)) if MAX_SPREAD_PCT else 0.0
    else:
        spread_ratio = 0.0

    return (oi_ratio + vol_ratio + spread_ratio) / 3


def market_strength(broad_efficiency_ratio_value) -> float:
    """NIFTY's own efficiency_ratio() -- broad-tape conviction proxy,
    independent of THIS symbol's own trend (see trend_strength())."""
    if broad_efficiency_ratio_value is None:
        return 0.0
    return _clamp01(broad_efficiency_ratio_value)


def htf_strength(mtf_confirmed: bool) -> float:
    return 1.0 if mtf_confirmed else 0.0


# ---------------------------------------------------------------------------
# Composite score + derived display metrics
# ---------------------------------------------------------------------------

def compute_trade_score(strengths: dict) -> int:
    """
    strengths keys must match config.TRADE_SCORE_WEIGHTS: trend,
    volume, oi, momentum, vwap, ema, risk_reward, liquidity,
    market_strength, htf -- each a 0-1 float. Missing keys score 0 for
    that factor rather than raising, matching example_quality_score()'s
    defensive .get(key, 0.0) convention in grading.py.
    """
    score = 0.0
    for key, weight in TRADE_SCORE_WEIGHTS.items():
        value = max(0.0, min(1.0, strengths.get(key, 0.0)))
        score += value * weight
    return round(score)


def compute_grade(trade_score: int) -> str:
    for threshold, grade in TRADE_SCORE_GRADE_THRESHOLDS:
        if trade_score >= threshold:
            return grade
    return "Reject"


def compute_probability_pct(trade_score: int) -> int:
    """
    Simple linear calibration, NOT a backtested probability model --
    same honesty as opportunity_score.py's documented approximations.
    Maps the 60-100 alertable range onto roughly 65%-97%, so a 92
    trade score reads close to the spec's own 92->91% example. Revisit
    once enough resolved trades exist to fit this against actual
    win-rate (the same win-rate check the old grade-vs-outcome
    feedback loop, mentioned in README.md, was built for).
    """
    return round(_clamp01((trade_score - 60) / 40) * 32 + 65)


def probability_label(probability_pct: int) -> str:
    """
    Bands compute_probability_pct()'s output per config.PROBABILITY_BANDS
    (Ignore / Watchlist / Potential Trade / High Probability Trade /
    Strong Institutional Setup) -- display/transparency only, walked
    the same way compute_grade() walks TRADE_SCORE_GRADE_THRESHOLDS.
    Not a second gate: config.TELEGRAM_MIN_PROBABILITY (checked by the
    caller, not here) is what actually decides whether a Telegram
    message goes out.
    """
    for threshold, label in PROBABILITY_BANDS:
        if probability_pct >= threshold:
            return label
    return "Ignore"


# Human-readable labels for the scoring-reasons breakdown below --
# deliberately separate from _CHECKLIST_LABELS further down, since that
# one is a flat ✅/⚠️ pass-mark list while this one needs a directional,
# case-specific phrase per factor (matching the phase-4 spec's "+15
# Trend Alignment" / "-5 Slightly High RSI" style example output).
_REASON_LABELS = {
    "trend": "Trend Alignment",
    "volume": "Volume Spike",
    "oi": "OI Build-up",
    "momentum": "Momentum",
    "vwap": "VWAP Positioning",
    "ema": "EMA Alignment",
    "risk_reward": "Risk:Reward",
    "liquidity": "Liquidity",
    "market_strength": "Market Strength",
    "htf": "Higher-Timeframe Confirmation",
}


def scoring_reasons(strengths: dict) -> list:
    """
    Turns the same 0-1 `strengths` dict compute_trade_score() consumes
    into a signed, per-factor points breakdown that sums to
    compute_trade_score()'s output -- e.g. "+15 Trend Alignment", "-5
    Liquidity". A factor contributes its full weight at strength 1.0;
    below half-strength it's shown as a NEGATIVE number (how many
    points short of that factor's own midpoint it is), so weak
    contributors read as visible deductions rather than just small
    positives -- matching the phase-4 spec's example mix of + and -
    lines. Sorted strongest-contribution-first. This is pure display
    math over numbers scanner.py already computed; it does not change
    compute_trade_score()'s own total.
    """
    reasons = []
    for key, weight in TRADE_SCORE_WEIGHTS.items():
        value = max(0.0, min(1.0, strengths.get(key, 0.0)))
        label = _REASON_LABELS.get(key, key)
        midpoint = weight / 2
        points = value * weight
        if points >= midpoint:
            reasons.append((points, f"+{round(points)} {label}"))
        else:
            reasons.append((points, f"-{round(midpoint - points)} {label}"))
    reasons.sort(key=lambda r: r[0], reverse=True)
    return [text for _, text in reasons]


def probability_trend(history_pct: list) -> str:
    """
    "Increasing" / "Stable" / "Decreasing" off the last two readings in
    a same-day probability history list (oldest-first, as maintained by
    state.py's probability_history). Fewer than 2 readings -> "Stable"
    (nothing to compare yet). A flat 1-point wobble either way still
    reads as "Stable" -- only a >=2-point move counts as a real
    direction change, so display doesn't flap on scan-to-scan noise.
    """
    if len(history_pct) < 2:
        return "Stable"
    delta = history_pct[-1] - history_pct[-2]
    if delta >= 2:
        return "Increasing"
    if delta <= -2:
        return "Decreasing"
    return "Stable"


def compute_confidence_score(trade_score: int) -> float:
    """Trade score /10, one decimal -- e.g. 92 -> 9.2."""
    return round(trade_score / 10, 1)


def compute_risk_score(atr_pct: float, moneyness: str, expiry_days: int) -> float:
    """
    0-10, HIGHER = riskier (matches the spec's "Risk: 2.3/10" example,
    where a low number is good). Same inputs grading.py's old
    risk_rating() used (ATR% of price, moneyness), plus days-to-expiry
    since theta risk compounds specifically near expiry (see
    filters.expiry_theta_ok()'s docstring).
    """
    score = 2.0  # baseline -- every options trade carries some risk
    if atr_pct >= ATR_VOLATILE_PCT_THRESHOLD:
        score += 3.0
    elif atr_pct >= ATR_VOLATILE_PCT_THRESHOLD * 0.5:
        score += 1.5

    if moneyness == "OTM":
        score += 2.0
    elif moneyness == "ITM":
        score -= 1.0

    if expiry_days is not None and expiry_days <= EXPIRY_DAY_THRESHOLD_DAYS:
        score += 1.5

    return round(max(0.5, min(10.0, score)), 1)


def compute_reward_score(rr_ratio: float) -> float:
    """0-10, HIGHER = better reward -- rr_ratio 3.0 (target 3x the SL
    distance) or more scores a full 10; scales linearly below that."""
    if rr_ratio is None or rr_ratio <= 0:
        return 0.0
    return round(min(10.0, (rr_ratio / 3.0) * 10), 1)


def confidence_display(grade: str) -> tuple:
    """Backward-compat stars/label pair, re-keyed to the new grade
    vocabulary -- trade dicts still carry confidence_stars/
    confidence_label alongside the new numeric confidence_score."""
    return CONFIDENCE_STARS_BY_TRADE_GRADE.get(grade, ("☆☆☆☆☆", "N/A"))


def risk_emoji_label(risk_score: float) -> tuple:
    """Emoji/label pair for risk_score (0-10, higher = riskier) --
    same three-bucket idea as grading.py's old risk_rating(), just
    driven off the new continuous risk_score instead of a fresh
    from-scratch calc."""
    if risk_score <= 3.0:
        return "🟢", "Low"
    if risk_score <= 6.0:
        return "🟡", "Medium"
    return "🔴", "High"


def expected_rr_text(entry_premium: float, stop_loss: float, target: float) -> str:
    """Formats as "1:X.X" per the spec's Telegram example. Returns
    "1:0" if the SL distance is zero/negative (shouldn't happen --
    dynamic_trade_levels() already floors SL below premium -- but this
    is display formatting, not a gate, so it degrades rather than
    raises)."""
    sl_distance = entry_premium - stop_loss
    target_distance = target - entry_premium
    if sl_distance <= 0:
        return "1:0"
    return f"1:{round(target_distance / sl_distance, 1)}"


_CHECKLIST_LABELS = [
    ("trend", "Trend"),
    ("volume", "Volume"),
    ("oi", "OI"),
    ("momentum", "Momentum"),
    ("vwap", "VWAP"),
    ("ema", "EMA"),
    ("risk_reward", "Risk Reward"),
    ("liquidity", "Liquidity"),
    ("market_strength", "Market Strength"),
    ("htf", "HTF"),
]


def factor_checklist(strengths: dict, pass_threshold: float = 0.5) -> list:
    """
    Telegram checklist lines (✅/⚠️ per factor) matching the spec's
    "Trend ✅ / Volume ✅ / ..." block. pass_threshold is a display
    cutoff only -- it does not gate whether the trade is alerted at
    all (config.ENTRY_SCORE on the composite trade_score already does
    that); a factor below threshold just shows ⚠️ instead of ✅ so the
    reader can see which specific inputs were weaker contributors.
    """
    lines = []
    for key, label in _CHECKLIST_LABELS:
        value = strengths.get(key, 0.0)
        mark = "✅" if value >= pass_threshold else "⚠️"
        lines.append(f"{label} {mark}")
    return lines
