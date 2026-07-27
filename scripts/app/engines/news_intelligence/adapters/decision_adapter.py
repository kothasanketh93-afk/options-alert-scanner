"""
app/engines/news_intelligence/adapters/decision_adapter.py -- the ONLY
file in this module aware that a Decision Engine exists elsewhere in the
project -- and even this file does not import or call it.
------------------------------------------------------------------------------
Per this module's objective ("DO NOT integrate. Only create an adapter"),
NewsDecisionAdapter.get_news_score() is a plain, standalone Python
function/class that scripts/app/engines/decision_engine/ can call FROM
THE OUTSIDE once integration is approved (see
docs/INTEGRATION_GUIDE.md) -- nothing in decision_engine/ is imported,
subclassed, or modified here, and nothing here is imported by
decision_engine/ yet. This file is the plug; the socket on the other side
does not exist until someone wires it in a separate change.

Output shape mirrors options_intelligence/engine.py's "Standard Output
Object" convention (a single flat, JSON-serializable dict) so that if/when
this IS wired in, decision_engine/ can treat setup["news_intelligence"]
the same additive way it already treats setup["options_intelligence"].
"""

from __future__ import annotations

from dataclasses import dataclass

from logging_setup import get_logger
from ..cache.news_cache import NewsCache, get_default_cache
from ..config import get_settings
from ..impact.impact_engine import compute_impact
from ..mapper.company_mapper import map_news_item
from ..models.impact import ImpactLevel
from ..models.sentiment import SentimentLabel
from ..sentiment.analyzer import analyze_news_item

_logger = get_logger("news_intelligence")

# Numeric weight per sentiment label, direction-aware (unlike impact,
# which is deliberately direction-agnostic -- see impact_engine.py).
_SENTIMENT_DIRECTION = {
    SentimentLabel.VERY_POSITIVE: 1.0,
    SentimentLabel.POSITIVE: 0.5,
    SentimentLabel.NEUTRAL: 0.0,
    SentimentLabel.NEGATIVE: -0.5,
    SentimentLabel.VERY_NEGATIVE: -1.0,
}

_IMPACT_WEIGHT = {
    ImpactLevel.VERY_HIGH: 1.0,
    ImpactLevel.HIGH: 0.75,
    ImpactLevel.MEDIUM: 0.5,
    ImpactLevel.LOW: 0.25,
    ImpactLevel.IGNORE: 0.0,
}


@dataclass
class NewsScoreResult:
    """The Standard Output Object this adapter returns. All fields are
    plain JSON-serializable types -- safe to drop straight into a
    Telegram message or a trade-setup dict without further conversion."""
    symbol: str
    news_score: float          # -100..100 (signed: direction * magnitude)
    sentiment: str
    impact: str
    confidence: float          # 0..1
    reasons: list
    article_count: int
    most_recent_headline: str = ""
    most_recent_url: str = ""

    def to_dict(self) -> dict:
        return dict(self.__dict__)


class NewsDecisionAdapter:
    """Thin, stateless-except-for-cache-reference facade. Construct once
    (or use the module-level default_adapter() singleton) and call
    get_news_score(symbol) as needed -- each call is a fast, in-memory
    read over the cache plus lexicon-based sentiment/impact math, no
    network I/O (collection already happened via engine.collect()/
    NewsScheduler)."""

    def __init__(self, cache: NewsCache = None, settings=None):
        self._cache = cache or get_default_cache()
        self._settings = settings or get_settings()

    def get_news_score(self, symbol: str, lookback_articles: int = 10) -> NewsScoreResult:
        symbol = symbol.upper().strip()
        items = self._cache.get_by_symbol(symbol, limit=lookback_articles)

        if not items:
            return NewsScoreResult(
                symbol=symbol, news_score=0.0, sentiment=SentimentLabel.NEUTRAL.value,
                impact=ImpactLevel.IGNORE.value, confidence=0.0,
                reasons=["No recent news found for this symbol"], article_count=0,
            )

        weighted_scores = []
        reasons: list[str] = []
        confidences = []
        dominant_impact = ImpactLevel.IGNORE
        dominant_sentiment = SentimentLabel.NEUTRAL

        for item in items:
            sentiment = analyze_news_item(item, settings=self._settings)
            mapping = map_news_item(
                item,
                min_confidence=self._settings.mapping.min_confidence,
                fuzzy_threshold=self._settings.mapping.fuzzy_match_threshold,
            )
            impact = compute_impact(
                category=item.category, sentiment=sentiment, mapping=mapping, settings=self._settings,
            )

            direction = _SENTIMENT_DIRECTION[sentiment.label]
            magnitude = _IMPACT_WEIGHT[impact.level]
            weighted_scores.append(direction * magnitude * 100 * impact.confidence)
            confidences.append(impact.confidence)

            if _IMPACT_WEIGHT[impact.level] >= _IMPACT_WEIGHT[dominant_impact]:
                dominant_impact = impact.level
                dominant_sentiment = sentiment.label

            reasons.append(
                f"[{item.source}] {item.headline[:80]} -> sentiment={sentiment.label.value} "
                f"impact={impact.level.value} conf={impact.confidence}"
            )

        news_score = round(sum(weighted_scores) / len(weighted_scores), 2) if weighted_scores else 0.0
        overall_confidence = round(sum(confidences) / len(confidences), 3) if confidences else 0.0

        result = NewsScoreResult(
            symbol=symbol,
            news_score=news_score,
            sentiment=dominant_sentiment.value,
            impact=dominant_impact.value,
            confidence=overall_confidence,
            reasons=reasons[:5],
            article_count=len(items),
            most_recent_headline=items[0].headline,
            most_recent_url=items[0].url or "",
        )
        _logger.info(
            f"NEWS_ADAPTER symbol={symbol} score={news_score} sentiment={result.sentiment} "
            f"impact={result.impact} confidence={overall_confidence} articles={len(items)}"
        )
        return result


_default_adapter: NewsDecisionAdapter = None


def default_adapter() -> NewsDecisionAdapter:
    global _default_adapter
    if _default_adapter is None:
        _default_adapter = NewsDecisionAdapter()
    return _default_adapter


def get_news_score(symbol: str, lookback_articles: int = 10) -> dict:
    """Module-level convenience wrapper -- the exact call shape given in
    this module's spec (`NewsDecisionAdapter.get_news_score(symbol)`),
    returned as a plain dict for the easiest possible future integration."""
    return default_adapter().get_news_score(symbol, lookback_articles=lookback_articles).to_dict()
