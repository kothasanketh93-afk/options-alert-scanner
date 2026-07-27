"""
app/engines/news_intelligence/sentiment/analyzer.py -- public sentiment
API: NewsItem -> SentimentResult.
"""

from __future__ import annotations

from ..config import get_settings
from ..models.news import NewsItem
from ..models.sentiment import SentimentLabel, SentimentResult
from . import scorer


def _label_for_score(score: float, cfg) -> SentimentLabel:
    if score >= cfg.very_positive_threshold:
        return SentimentLabel.VERY_POSITIVE
    if score >= cfg.positive_threshold:
        return SentimentLabel.POSITIVE
    if score <= cfg.very_negative_threshold:
        return SentimentLabel.VERY_NEGATIVE
    if score <= cfg.negative_threshold:
        return SentimentLabel.NEGATIVE
    return SentimentLabel.NEUTRAL


def analyze_text(text: str, settings=None) -> SentimentResult:
    settings = settings or get_settings()
    cfg = settings.sentiment

    score, positive_hits, negative_hits = scorer.raw_score(text)
    hit_count = len(positive_hits) + len(negative_hits)
    confidence = scorer.confidence_from_hits(hit_count, len(text or ""))
    confidence = max(confidence, cfg.min_confidence if hit_count else confidence)

    label = _label_for_score(score, cfg)

    reasons = []
    if positive_hits:
        reasons.append(f"Positive phrases detected: {', '.join(positive_hits[:5])}")
    if negative_hits:
        reasons.append(f"Negative phrases detected: {', '.join(negative_hits[:5])}")
    if not reasons:
        reasons.append("No strong sentiment phrases detected -- treated as neutral")

    return SentimentResult(
        label=label,
        score=round(score, 3),
        confidence=confidence,
        reasons=reasons,
        positive_hits=positive_hits,
        negative_hits=negative_hits,
    )


def analyze_news_item(item: NewsItem, settings=None) -> SentimentResult:
    return analyze_text(f"{item.headline}. {item.summary}", settings=settings)
