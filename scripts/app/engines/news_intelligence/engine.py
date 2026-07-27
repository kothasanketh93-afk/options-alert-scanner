"""
app/engines/news_intelligence/engine.py -- NewsEngine, the ONE class the
rest of the world (once integrated) is meant to import.
------------------------------------------------------------------------------
    from app.engines.news_intelligence import NewsEngine

    engine = NewsEngine()
    engine.collect()
    engine.get_news_score("RELIANCE")

Mirrors options_intelligence's "one entry point" convention
(engine.analyze()) and early_breakout's engine.py -- every sibling package
in this module (collector, mapper, sentiment, impact, cache, adapters) is
independently importable/testable on its own, but NewsEngine is the
single facade a new caller should reach for first.

Python API only -- no REST/FastAPI/Flask anywhere in this package, per
this module's objective.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from logging_setup import get_logger
from .adapters.decision_adapter import NewsDecisionAdapter
from .cache.news_cache import NewsCache, get_default_cache
from .collector.collector import CollectionResult, collect_all
from .collector.scheduler import NewsScheduler
from .config import get_settings, reload_settings
from .impact.impact_engine import compute_impact
from .mapper.company_mapper import map_news_item
from .models.news import NewsItem
from .sentiment.analyzer import analyze_news_item

_logger = get_logger("news_intelligence")


class NewsEngine:
    """Owns one NewsCache (defaults to the process-wide singleton -- pass
    `cache=NewsCache(...)` for an isolated instance, which tests do) and
    exposes collection + scoring + a background scheduler over it."""

    def __init__(self, cache: Optional[NewsCache] = None, settings=None):
        self.settings = settings or get_settings()
        self.cache = cache or get_default_cache()
        self._adapter = NewsDecisionAdapter(cache=self.cache, settings=self.settings)
        self._scheduler: Optional[NewsScheduler] = None

    # --- collection ----------------------------------------------------

    def collect(self) -> CollectionResult:
        """One synchronous collection cycle across every enabled
        provider. Safe to call from a plain script/cron entry point."""
        return collect_all(cache=self.cache, settings=self.settings)

    async def collect_async(self) -> CollectionResult:
        """Async-compatible entry point -- runs the same (blocking,
        thread-pooled) collect_all() in the default executor so an
        asyncio-based host never blocks its event loop calling this."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.collect)

    # --- scoring ---------------------------------------------------------

    def get_news_score(self, symbol: str, lookback_articles: int = 10) -> dict:
        """The Part 11.5 headline function -- see
        adapters/decision_adapter.py for the full scoring logic. Returns
        a plain JSON-serializable dict (NewsScoreResult.to_dict())."""
        return self._adapter.get_news_score(symbol, lookback_articles=lookback_articles).to_dict()

    def analyze_item(self, item: NewsItem) -> dict:
        """Runs mapping + sentiment + impact on one NewsItem directly
        (bypassing the cache) -- useful for ad-hoc analysis of a single
        headline/article without a full collection cycle."""
        mapping = map_news_item(
            item, min_confidence=self.settings.mapping.min_confidence,
            fuzzy_threshold=self.settings.mapping.fuzzy_match_threshold,
        )
        sentiment = analyze_news_item(item, settings=self.settings)
        impact = compute_impact(category=item.category, sentiment=sentiment, mapping=mapping, settings=self.settings)
        return {
            "news_id": item.news_id,
            "headline": item.headline,
            "companies": mapping.companies,
            "symbols": mapping.symbols,
            "sectors": mapping.sectors,
            "mapping_confidence": mapping.confidence,
            "sentiment": sentiment.to_dict(),
            "impact": impact.to_dict(),
        }

    # --- cache access ----------------------------------------------------

    def get_recent_news(self, symbol: str, limit: int = 20) -> list[dict]:
        return [item.to_dict() for item in self.cache.get_by_symbol(symbol, limit=limit)]

    def cache_size(self) -> int:
        return self.cache.size()

    # --- scheduler (opt-in background polling) --------------------------

    def start_background_collection(self, interval_seconds: Optional[int] = None) -> None:
        interval = interval_seconds or self.settings.collector.scheduler_default_interval_seconds
        if self._scheduler is None:
            self._scheduler = NewsScheduler(self.collect, interval_seconds=interval)
        self._scheduler.start()

    def stop_background_collection(self) -> None:
        if self._scheduler is not None:
            self._scheduler.stop()

    # --- config ------------------------------------------------------

    def reload_config(self) -> None:
        self.settings = reload_settings()
        self._adapter = NewsDecisionAdapter(cache=self.cache, settings=self.settings)
        _logger.info("NEWS_ENGINE settings reloaded")
