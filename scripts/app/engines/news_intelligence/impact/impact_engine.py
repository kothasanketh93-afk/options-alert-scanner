"""
app/engines/news_intelligence/impact/impact_engine.py -- expected market
impact of a NewsItem + its SentimentResult.
------------------------------------------------------------------------------
Impact is deliberately a function of (category, sentiment magnitude,
mapping confidence) -- NOT of sentiment DIRECTION. A very negative fraud
allegation and a very positive earnings beat can carry the same impact
level (both move the stock a lot); direction belongs to sentiment, not
impact. Downstream consumers (the decision adapter) combine both.
"""

from __future__ import annotations

from ..config import get_settings
from ..mapper.company_mapper import MappingResult
from ..models.impact import ImpactLevel, ImpactResult
from ..models.news import NewsCategory
from ..models.sentiment import SentimentResult
from . import confidence as confidence_mod

_DURATION_BY_CATEGORY: dict[NewsCategory, str] = {
    NewsCategory.EARNINGS: "1-2 days",
    NewsCategory.REGULATORY: "3-7 days",
    NewsCategory.MONETARY_POLICY: "1-3 days (market-wide)",
    NewsCategory.MERGER_ACQUISITION: "1-4 weeks",
    NewsCategory.CORPORATE_ACTION: "intraday to 2 days",
    NewsCategory.LEGAL: "1-2 weeks",
    NewsCategory.CORPORATE_ANNOUNCEMENT: "intraday",
    NewsCategory.GOVERNMENT: "1-3 days (market-wide)",
    NewsCategory.MARKET_GENERAL: "intraday",
    NewsCategory.UNKNOWN: "intraday",
}

_URGENCY_BY_LEVEL = {
    ImpactLevel.VERY_HIGH: "immediate",
    ImpactLevel.HIGH: "immediate",
    ImpactLevel.MEDIUM: "same_day",
    ImpactLevel.LOW: "monitor",
    ImpactLevel.IGNORE: "monitor",
}


def _level_for_score(score: float, cfg) -> ImpactLevel:
    if score >= cfg.very_high_score:
        return ImpactLevel.VERY_HIGH
    if score >= cfg.high_score:
        return ImpactLevel.HIGH
    if score >= cfg.medium_score:
        return ImpactLevel.MEDIUM
    if score >= cfg.low_score:
        return ImpactLevel.LOW
    return ImpactLevel.IGNORE


def compute_impact(*, category: NewsCategory, sentiment: SentimentResult,
                    mapping: MappingResult, settings=None) -> ImpactResult:
    settings = settings or get_settings()
    cfg = settings.impact

    category_weight = float(cfg.category_weights.get(category.value, 0.3))
    sentiment_magnitude = abs(sentiment.score)  # direction-agnostic, see module docstring

    # 0..100 composite: category baseline (how impactful this KIND of news
    # tends to be) scaled up by how strong the sentiment signal was and how
    # confident we are in both the mapping and the sentiment read.
    raw = (
        category_weight * 55
        + sentiment_magnitude * 30
        + mapping.confidence * 15
    )
    score = round(max(0.0, min(100.0, raw)), 1)

    level = _level_for_score(score, cfg)
    duration = _DURATION_BY_CATEGORY.get(category, "intraday")
    urgency = _URGENCY_BY_LEVEL[level]

    conf = confidence_mod.impact_confidence(
        mapping_confidence=mapping.confidence,
        sentiment_confidence=sentiment.confidence,
        category_weight=category_weight,
        has_symbol_match=bool(mapping.symbols),
    )

    reasons = [
        f"Category '{category.value}' baseline weight={category_weight}",
        f"Sentiment magnitude={round(sentiment_magnitude, 3)} ({sentiment.label.value})",
        f"Company mapping confidence={mapping.confidence}" if mapping.symbols
        else "No confident company/symbol match -- impact capped",
    ]

    return ImpactResult(
        level=level,
        score=score,
        expected_duration=duration,
        urgency=urgency,
        confidence=conf,
        reasons=reasons,
    )
