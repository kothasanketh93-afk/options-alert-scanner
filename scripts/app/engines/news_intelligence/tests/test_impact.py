"""
app/engines/news_intelligence/tests/test_impact.py
Run: python scripts/app/engines/news_intelligence/tests/test_impact.py
"""

import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
_scripts_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_here))))
sys.path.insert(0, _scripts_dir)

from app.engines.news_intelligence.impact.impact_engine import compute_impact
from app.engines.news_intelligence.mapper.company_mapper import MappingResult
from app.engines.news_intelligence.models.impact import ImpactLevel
from app.engines.news_intelligence.models.news import NewsCategory
from app.engines.news_intelligence.models.sentiment import SentimentLabel, SentimentResult


def check(condition, message):
    assert condition, f"FAILED: {message}"
    print(f"PASS: {message}")


def _sentiment(score, confidence, label):
    return SentimentResult(label=label, score=score, confidence=confidence)


def test_earnings_with_strong_confident_match_is_high_impact():
    sentiment = _sentiment(0.8, 0.9, SentimentLabel.VERY_POSITIVE)
    mapping = MappingResult(companies=["Reliance Industries"], symbols=["RELIANCE"], sectors=["Energy"], confidence=1.0)
    result = compute_impact(category=NewsCategory.EARNINGS, sentiment=sentiment, mapping=mapping)
    check(result.level in (ImpactLevel.HIGH, ImpactLevel.VERY_HIGH),
          "confident strong-sentiment earnings news scores HIGH or VERY_HIGH impact")
    check(0 <= result.score <= 100, "impact score is within 0-100")


def test_direction_agnostic_scoring():
    positive = _sentiment(0.8, 0.9, SentimentLabel.VERY_POSITIVE)
    negative = _sentiment(-0.8, 0.9, SentimentLabel.VERY_NEGATIVE)
    mapping = MappingResult(companies=["X"], symbols=["X"], sectors=["Y"], confidence=1.0)
    result_pos = compute_impact(category=NewsCategory.EARNINGS, sentiment=positive, mapping=mapping)
    result_neg = compute_impact(category=NewsCategory.EARNINGS, sentiment=negative, mapping=mapping)
    check(result_pos.level == result_neg.level,
          "equal-magnitude positive and negative sentiment produce the same impact LEVEL")
    check(abs(result_pos.score - result_neg.score) < 0.01,
          "impact score is direction-agnostic (magnitude only)")


def test_no_symbol_match_caps_impact_confidence():
    sentiment = _sentiment(0.9, 0.9, SentimentLabel.VERY_POSITIVE)
    mapping = MappingResult()  # no symbols
    result = compute_impact(category=NewsCategory.MARKET_GENERAL, sentiment=sentiment, mapping=mapping)
    check(result.confidence <= 0.35, "impact confidence is capped low with no confident company match")


def test_market_general_low_category_weight_trends_toward_low_impact():
    sentiment = _sentiment(0.1, 0.3, SentimentLabel.NEUTRAL)
    mapping = MappingResult(symbols=["X"], confidence=0.5)
    result = compute_impact(category=NewsCategory.MARKET_GENERAL, sentiment=sentiment, mapping=mapping)
    check(result.level in (ImpactLevel.LOW, ImpactLevel.IGNORE, ImpactLevel.MEDIUM),
          "weak sentiment + low-weight category does not spuriously score as HIGH/VERY_HIGH")


if __name__ == "__main__":
    tests = [obj for name, obj in list(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
    print(f"\n{len(tests)} test functions passed.")
