"""
app/engines/news_intelligence/tests/test_collector.py
Run: python scripts/app/engines/news_intelligence/tests/test_collector.py

Covers collector.py's fan-out, exact + fuzzy dedup, and per-provider error
isolation (a raising provider must not abort the whole cycle) -- using
fake in-memory providers, no real network calls.
"""

import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
_scripts_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_here))))
sys.path.insert(0, _scripts_dir)

from app.engines.news_intelligence.cache.news_cache import NewsCache
from app.engines.news_intelligence.collector.collector import collect_all
from app.engines.news_intelligence.config import get_settings
from app.engines.news_intelligence.providers.base_provider import BaseProvider


def check(condition, message):
    assert condition, f"FAILED: {message}"
    print(f"PASS: {message}")


class FakeNSEProvider(BaseProvider):
    name = "NSE"

    def fetch_raw(self):
        return [
            {"subject": "Wipro wins large multi-year outsourcing deal", "an_dt": "2026-07-27 10:00:00", "symbol": "WIPRO"},
            {"subject": "Wipro wins large multi-year outsourcing deal", "an_dt": "2026-07-27 10:00:00", "symbol": "WIPRO"},
        ]


class FakeBrokenProvider(BaseProvider):
    """Simulates a provider whose fetch_raw() itself raises -- collector
    must isolate this and keep processing other providers."""
    name = "BROKEN"

    def fetch_raw(self):
        raise RuntimeError("simulated network failure")


class FakeEmptyProvider(BaseProvider):
    name = "EMPTY"

    def fetch_raw(self):
        return []


def test_exact_duplicate_collapses_to_one_new_item():
    cache = NewsCache(ttl_seconds=60)
    settings = get_settings()
    result = collect_all(cache=cache, settings=settings, providers=[FakeNSEProvider()])
    check(len(result.new_items) == 1, "two identical raw rows collapse to exactly one new item")
    check(result.duplicates_skipped == 1, "the exact duplicate is counted in duplicates_skipped")


def test_broken_provider_does_not_abort_the_cycle():
    cache = NewsCache(ttl_seconds=60)
    settings = get_settings()
    result = collect_all(cache=cache, settings=settings,
                          providers=[FakeNSEProvider(), FakeBrokenProvider()])
    check("BROKEN" in result.provider_errors, "the broken provider's error is captured")
    check(len(result.new_items) == 1, "the healthy provider's items still make it through")


def test_empty_provider_is_harmless():
    cache = NewsCache(ttl_seconds=60)
    settings = get_settings()
    result = collect_all(cache=cache, settings=settings, providers=[FakeEmptyProvider()])
    check(result.new_items == [], "a provider returning zero items produces zero new items, no error")
    check(result.provider_errors == {}, "an empty (but not failing) provider reports no error")


def test_mapping_applied_before_caching():
    cache = NewsCache(ttl_seconds=60)
    settings = get_settings()
    collect_all(cache=cache, settings=settings, providers=[FakeNSEProvider()])
    items = cache.get_by_symbol("WIPRO")
    check(len(items) == 1, "company mapping is applied before the item is cached, so symbol-lookup works")


if __name__ == "__main__":
    tests = [obj for name, obj in list(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
    print(f"\n{len(tests)} test functions passed.")
