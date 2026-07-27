"""
app/engines/news_intelligence/tests/test_performance.py
Run: python scripts/app/engines/news_intelligence/tests/test_performance.py

Sanity-checks the "thousands of news items/day" production requirement --
not a rigorous benchmark suite, but enough to catch an accidental O(n^2)
regression in the hot paths (parsing, sentiment, cache put/lookup).
"""

import os
import sys
import time

_here = os.path.dirname(os.path.abspath(__file__))
_scripts_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_here))))
sys.path.insert(0, _scripts_dir)

from app.engines.news_intelligence.cache.news_cache import NewsCache
from app.engines.news_intelligence.models.news import NewsCategory, NewsItem
from app.engines.news_intelligence.parser.parser import parse_batch
from app.engines.news_intelligence.sentiment.analyzer import analyze_text
from app.engines.news_intelligence.utils.time_utils import now_ist_iso


def check(condition, message):
    assert condition, f"FAILED: {message}"
    print(f"PASS: {message}")


def test_parse_batch_handles_thousands_quickly():
    raw_rows = [
        {"subject": f"Company {i} reports quarterly results", "an_dt": "27-Jul-2026", "symbol": f"SYM{i}"}
        for i in range(3000)
    ]
    started = time.time()
    parsed = parse_batch("NSE", raw_rows)
    elapsed = time.time() - started
    check(len(parsed) == 3000, "3000 raw rows all parse successfully")
    check(elapsed < 5.0, f"parsing 3000 rows completes in under 5s (took {elapsed:.2f}s)")


def test_sentiment_analysis_scales_linearly_enough():
    texts = [f"Company {i} reports record profit and beats estimates with strong growth" for i in range(2000)]
    started = time.time()
    for text in texts:
        analyze_text(text)
    elapsed = time.time() - started
    check(elapsed < 5.0, f"analyzing 2000 headlines completes in under 5s (took {elapsed:.2f}s)")


def test_cache_put_and_lookup_at_scale():
    cache = NewsCache(ttl_seconds=3600, max_items=20000)
    started = time.time()
    for i in range(10000):
        item = NewsItem(
            news_id=f"perf-{i}", headline=f"Headline {i}", summary="Summary",
            source="TEST", published_at=now_ist_iso(), fetched_at=now_ist_iso(),
            category=NewsCategory.CORPORATE_ANNOUNCEMENT, symbols=[f"SYM{i % 50}"],
        )
        cache.put(item)
    put_elapsed = time.time() - started
    check(cache.size() == 10000, "10,000 unique items all land in the cache")
    check(put_elapsed < 5.0, f"10,000 cache puts complete in under 5s (took {put_elapsed:.2f}s)")

    started = time.time()
    for _ in range(1000):
        cache.get_by_symbol("SYM7", limit=50)
    lookup_elapsed = time.time() - started
    check(lookup_elapsed < 2.0, f"1000 symbol lookups against a 10k-item cache complete in under 2s (took {lookup_elapsed:.2f}s)")


if __name__ == "__main__":
    tests = [obj for name, obj in list(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
    print(f"\n{len(tests)} test functions passed.")
