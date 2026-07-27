"""
app/engines/news_intelligence/tests/test_sentiment.py
Run: python scripts/app/engines/news_intelligence/tests/test_sentiment.py
"""

import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
_scripts_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_here))))
sys.path.insert(0, _scripts_dir)

from app.engines.news_intelligence.models.sentiment import SentimentLabel
from app.engines.news_intelligence.sentiment.analyzer import analyze_text


def check(condition, message):
    assert condition, f"FAILED: {message}"
    print(f"PASS: {message}")


def test_positive_earnings_headline():
    result = analyze_text(
        "Company reports record profit and beats estimates with strong growth in revenue"
    )
    check(result.label in (SentimentLabel.POSITIVE, SentimentLabel.VERY_POSITIVE),
          "strong positive earnings language scores as positive/very_positive")
    check(result.score > 0, "positive text has a positive score")
    check(len(result.positive_hits) > 0, "positive phrases are recorded in positive_hits")


def test_negative_fraud_headline():
    result = analyze_text(
        "SEBI orders investigation into fraud and penalty imposed after probe finds violations"
    )
    check(result.label in (SentimentLabel.NEGATIVE, SentimentLabel.VERY_NEGATIVE),
          "fraud/penalty/investigation language scores as negative/very_negative")
    check(result.score < 0, "negative text has a negative score")


def test_neutral_text_has_low_confidence():
    result = analyze_text("The company will hold its annual general meeting next month")
    check(result.label == SentimentLabel.NEUTRAL, "text with no sentiment keywords is NEUTRAL")
    check(result.confidence <= 0.3, "neutral-by-absence has low confidence, not false certainty")


def test_confidence_is_bounded():
    result = analyze_text("Massive fraud, huge scam, complete bankruptcy, total disaster, penalty, fine")
    check(0.0 <= result.confidence <= 1.0, "confidence stays within [0, 1] even for a dense hit text")
    check(-1.0 <= result.score <= 1.0, "score stays within [-1, 1] even for a dense hit text")


def test_empty_text_is_safe():
    result = analyze_text("")
    check(result.label == SentimentLabel.NEUTRAL, "empty text does not raise, returns NEUTRAL")


if __name__ == "__main__":
    tests = [obj for name, obj in list(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
    print(f"\n{len(tests)} test functions passed.")
