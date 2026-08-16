"""
scoring.py -- Options Confirmation Engine, Options Scoring Engine,
Options Filter Engine.
------------------------------------------------------------------------------
Combines every sub-score chain_metrics.py/greeks_iv.py/
liquidity_premium.py/strike_expiry.py/momentum.py produced into one
consolidated Options Intelligence Score (0-100) and a pass/fail
decision -- engine.py's analyze() is the only caller that needs to
know about every module above; this file is the aggregation layer.
"""

from config import OPTIONS_INTELLIGENCE_WEIGHTS, OPTIONS_INTELLIGENCE_MIN_SCORE


def options_confidence(sub_scores: dict, min_confirm: float = 0.5) -> str:
    """
    Options Confirmation Engine: "Confirmed" only when EVERY
    directional-relevance factor (oi, pcr, max_pain, greeks, iv,
    liquidity, momentum -- the ones capable of actively CONTRADICTING a
    direction) clears min_confirm; "Partial" if most do; "Weak"
    otherwise. strike/expiry are excluded here -- they're quality-of-
    setup reads, not directional confirmation signals.

    BUGFIX (2026-08-15, P0 audit follow-up): a factor that's PRESENT in
    `sub_scores` but None (e.g. liquidity_score() now correctly returns
    None for missing/invalid bid/ask) used to crash this function's
    `v >= min_confirm` comparison -- `sub_scores.get(k, 0.5)` only
    supplies its default for an ABSENT key, never for a present-but-None
    value. Now explicitly excludes None-valued factors from
    consideration (same "insufficient evidence, not counted either way"
    principle applied throughout this session), rather than crashing or
    silently treating unavailable evidence as confirming/denying.
    """
    directional_keys = ("oi", "pcr", "max_pain", "greeks", "iv", "liquidity", "premium", "momentum")
    values = [sub_scores[k] for k in directional_keys if k in sub_scores and sub_scores[k] is not None]
    if not values:
        return "Weak"
    passing = sum(1 for v in values if v >= min_confirm)
    ratio = passing / len(values)
    if ratio >= 0.85:
        return "Confirmed"
    if ratio >= 0.6:
        return "Partial"
    return "Weak"


def options_intelligence_score(sub_scores: dict) -> int:
    """
    Weighted composite over config.OPTIONS_INTELLIGENCE_WEIGHTS (sums
    to 100). Renormalizes over whatever sub-scores are actually
    available (not None), same "None means excluded, not zero"
    principle as trade_score.compute_trade_score().

    BUGFIX (2026-08-15, P0 audit follow-up): this docstring used to
    claim "same defensive .get(key, 0.0) convention... a sub-score this
    run couldn't compute just contributes 0 instead of crashing" -- but
    `sub_scores.get(key, 0.0)` only supplies 0.0 when `key` is ABSENT;
    every sub-score key here is always PRESENT (just sometimes None),
    so that claim was never actually true, and `min(1.0, None)` (inside
    the old clamp) raised TypeError the first time a genuinely None
    sub-score reached this function (confirmed via liquidity_score()
    once missing bid/ask stopped being silently defaulted to 0.0).
    """
    total = 0.0
    available_weight = 0.0
    for key, weight in OPTIONS_INTELLIGENCE_WEIGHTS.items():
        value = sub_scores.get(key)
        if value is None:
            continue
        total += max(0.0, min(1.0, value)) * weight
        available_weight += weight
    if available_weight <= 0:
        return 0
    return round(total * (sum(OPTIONS_INTELLIGENCE_WEIGHTS.values()) / available_weight))


_REJECTION_LABELS = {
    "liquidity": "Poor Liquidity",
    "greeks": "Unfavorable Greeks (high gamma/theta risk)",
    "iv": "Abnormal IV",
    "premium": "Poor Premium Quality (mostly time value)",
    "oi": "Weak OI",
}


def options_filter(score: int, sub_scores: dict, iv_abnormal: bool = False) -> tuple:
    """
    Options Filter Engine: returns (accepted: bool, reasons: list).
    Rejects when the consolidated score is below
    config.OPTIONS_INTELLIGENCE_MIN_SCORE, OR when any individual
    hard-hygiene sub-score (liquidity/greeks/iv/premium/oi) is very
    poor (<0.2) even if other factors propped the composite up above
    the floor -- mirrors filters.quality_gate()'s "a few things must
    never be papered over by a good weighted average" principle,
    scoped to Part 5's own sub-scores only (it does not re-check
    filters.py's existing hard gates, which already ran upstream).

    BUGFIX (2026-08-15, P0 audit follow-up): `sub_scores.get(key, 1.0)`
    only supplies its default when `key` is genuinely ABSENT -- every
    key here is always PRESENT (just sometimes None now, e.g.
    liquidity_score() correctly returning None for missing/invalid
    bid/ask), so `None < 0.2` used to raise TypeError. A HARD-HYGIENE
    factor being genuinely UNAVAILABLE is treated as a rejection here
    too (fail closed) -- not excluded/renormalized the way the softer
    composite score above handles None -- consistent with filters.
    liquidity_ok()'s own fail-closed posture for the same missing-quote
    situation: insufficient evidence for a hard-hygiene check is not
    the same as "fine," and must not silently pass.
    """
    reasons = []
    if iv_abnormal:
        reasons.append(_REJECTION_LABELS["iv"])
    for key, label in _REJECTION_LABELS.items():
        if key == "iv":
            continue
        value = sub_scores.get(key)
        if value is None:
            reasons.append(f"{label} (data unavailable)")
        elif value < 0.2:
            reasons.append(label)
    if score < OPTIONS_INTELLIGENCE_MIN_SCORE:
        reasons.append(f"Options Intelligence Score below floor ({score}/{OPTIONS_INTELLIGENCE_MIN_SCORE})")

    accepted = not reasons
    return accepted, (reasons or ["Passed"])
