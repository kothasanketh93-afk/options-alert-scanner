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
    """
    directional_keys = ("oi", "pcr", "max_pain", "greeks", "iv", "liquidity", "premium", "momentum")
    values = [sub_scores.get(k, 0.5) for k in directional_keys if k in sub_scores]
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
    to 100) -- same defensive .get(key, 0.0) convention as
    trade_score.compute_trade_score()/grading.example_quality_score(),
    so a sub-score this run couldn't compute (rather than raising)
    just contributes 0 for its slice instead of crashing the whole
    composite.
    """
    total = 0.0
    for key, weight in OPTIONS_INTELLIGENCE_WEIGHTS.items():
        value = max(0.0, min(1.0, sub_scores.get(key, 0.0)))
        total += value * weight
    return round(total)


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
    """
    reasons = []
    if iv_abnormal:
        reasons.append(_REJECTION_LABELS["iv"])
    for key, label in _REJECTION_LABELS.items():
        if key == "iv":
            continue
        if sub_scores.get(key, 1.0) < 0.2:
            reasons.append(label)
    if score < OPTIONS_INTELLIGENCE_MIN_SCORE:
        reasons.append(f"Options Intelligence Score below floor ({score}/{OPTIONS_INTELLIGENCE_MIN_SCORE})")

    accepted = not reasons
    return accepted, (reasons or ["Passed"])
