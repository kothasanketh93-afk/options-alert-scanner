"""
app/engines/news_intelligence/collector/collector.py -- fans out over all
enabled providers, parses, dedups, and caches one collection cycle's
worth of news.
------------------------------------------------------------------------------
Uses a ThreadPoolExecutor (not asyncio) for the fan-out: every provider's
fetch_raw() ultimately calls `requests` (a blocking library), so a thread
pool is the correct concurrency primitive here -- see
utils/http_client.py's docstring for why this package doesn't adopt
aiohttp just to be "async" on paper. engine.py's async-facing methods
(collect_async()) wrap this same thread pool via
loop.run_in_executor(), which is what "Async Compatible" means for this
package (see docs/ARCHITECTURE.md).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from logging_setup import get_logger
from ..cache.news_cache import NewsCache, get_default_cache
from ..config import get_settings
from ..mapper.company_mapper import map_news_item
from ..models.news import NewsItem
from ..parser.parser import parse_batch
from ..providers import BaseProvider, get_enabled_providers
from . import deduplicator

_logger = get_logger("news_intelligence")


class CollectionResult:
    """Summary of one collect_all() cycle -- returned so engine.py/tests
    can report counts without re-querying the cache."""

    def __init__(self):
        self.new_items: list[NewsItem] = []
        self.duplicates_skipped: int = 0
        self.provider_errors: dict[str, str] = {}
        self.raw_counts: dict[str, int] = {}

    def __repr__(self) -> str:
        return (f"<CollectionResult new={len(self.new_items)} "
                f"duplicates={self.duplicates_skipped} errors={self.provider_errors}>")

    def degraded_providers(self) -> list[str]:
        """FIX (2026-07-31): providers that returned 0 items WITHOUT
        raising (e.g. an API's payload shape changed, a page moved,
        a feed's region ID is stale) never show up in
        `provider_errors` -- that dict only tracks providers whose
        fetch_raw()/parse_batch() actually threw (see
        _run_one_provider()'s try/except below). That is by design
        (a caught, logged, gracefully-degraded 0 is not the same kind
        of failure as an unhandled exception), but it meant the
        "cycle complete ... errors=none" summary line could read as
        "everything's fine" on a run where 2-3 of 5 providers were
        silently contributing nothing. This does not change collection,
        dedup, or caching behavior at all -- it only changes what the
        summary log line reports."""
        return [name for name, count in self.raw_counts.items()
                if count == 0 and name not in self.provider_errors]


def _apply_mapping(item: NewsItem, settings) -> NewsItem:
    """Populates item.companies/symbols/sectors in-place (NewsItem is a
    plain mutable dataclass -- see models/news.py) BEFORE the item ever
    reaches the cache, so the cache's symbol-index and the fuzzy
    deduplicator (which compares near-duplicates per-symbol) both have
    something to key on. Mapping confidence itself is not stored on
    NewsItem -- callers needing it (engine.get_news_score) recompute via
    mapper.map_news_item on demand, which is cheap (in-process lexicon
    lookup, no I/O)."""
    mapping = map_news_item(
        item,
        min_confidence=settings.mapping.min_confidence,
        fuzzy_threshold=settings.mapping.fuzzy_match_threshold,
    )
    item.companies = mapping.companies
    item.symbols = mapping.symbols
    item.sectors = mapping.sectors
    return item


def _run_one_provider(provider: BaseProvider, max_items: int) -> tuple[str, list[NewsItem], Optional[str]]:
    try:
        raw_items = provider.fetch_raw()[:max_items]
        parsed = parse_batch(provider.name, raw_items)
        return provider.name, parsed, None
    except Exception as exc:  # noqa: BLE001 -- one provider's failure must not abort the cycle
        _logger.error(f"NEWS_COLLECTOR provider={provider.name} raised unexpectedly: {exc}")
        return provider.name, [], str(exc)


def collect_all(*, cache: Optional[NewsCache] = None, settings=None,
                 providers: Optional[list[BaseProvider]] = None) -> CollectionResult:
    """One synchronous collection cycle across every enabled provider.
    Safe to call directly (e.g. from a cron-triggered script, mirroring
    the project's own cron-job.org + GitHub Actions trigger pattern) or
    from scheduler.py's background loop."""
    settings = settings or get_settings()
    cache = cache or get_default_cache()
    providers = providers if providers is not None else get_enabled_providers(settings)

    result = CollectionResult()
    max_items = settings.collector.max_items_per_cycle_per_provider
    workers = max(1, settings.collector.fan_out_workers)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_run_one_provider, p, max_items): p for p in providers}
        for future in as_completed(futures):
            provider_name, parsed_items, error = future.result()
            result.raw_counts[provider_name] = len(parsed_items)
            if error:
                result.provider_errors[provider_name] = error
                continue

            for item in parsed_items:
                item = _apply_mapping(item, settings)

                if settings.dedup.exact_hash_enabled and cache.contains(item.news_id):
                    result.duplicates_skipped += 1
                    continue
                if settings.dedup.fuzzy_similarity_enabled and deduplicator.is_near_duplicate(
                    item, cache,
                    threshold=settings.dedup.fuzzy_similarity_threshold,
                    window_minutes=settings.dedup.fuzzy_window_minutes,
                ):
                    result.duplicates_skipped += 1
                    continue

                if cache.put(item):
                    result.new_items.append(item)
                else:
                    result.duplicates_skipped += 1

    degraded = result.degraded_providers()
    _logger.info(
        f"NEWS_COLLECTOR cycle complete new={len(result.new_items)} "
        f"duplicates={result.duplicates_skipped} errors={result.provider_errors or 'none'} "
        f"degraded={degraded or 'none'}"
    )
    return result
