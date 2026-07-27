"""
app/engines/news_intelligence/impact/confidence.py -- how sure the Impact
Engine is about its own score.
------------------------------------------------------------------------------
Deliberately separate from impact_engine.py's score math (same
score-module/confidence-module split as decision_engine/confidence.py in
the existing project) -- a caller may want to gate on confidence
independently of the impact level itself (e.g. surface only "high
confidence" alerts even at MEDIUM impact).
"""

from __future__ import annotations


def impact_confidence(*, mapping_confidence: float, sentiment_confidence: float,
                       category_weight: float, has_symbol_match: bool) -> float:
    """Blends: how sure we are which company this is about, how sure the
    sentiment read is, and whether the category itself is inherently a
    reliable impact predictor (category_weight, from
    config.impact.category_weights). A news item with no symbol match at
    all is capped low -- impact without a company to point it at is not
    actionable for the decision-engine adapter."""
    if not has_symbol_match:
        return round(min(0.35, 0.15 + 0.2 * sentiment_confidence), 3)

    blended = (0.45 * mapping_confidence) + (0.35 * sentiment_confidence) + (0.20 * category_weight)
    return round(max(0.0, min(1.0, blended)), 3)
