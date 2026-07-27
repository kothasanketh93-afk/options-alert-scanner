"""
app/engines/news_intelligence/tests/test_cache.py
Run: python scripts/app/engines/news_intelligence/tests/test_cache.py
"""

import os
import sys
import threading
import time

_here = os.path.dirname(os.path.abspath(__file__))
_scripts_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_here))))
sys.path.insert(0, _scripts_dir)

from app.engines.news_intelligence.cache.news_cache import NewsCache
from app.engines.news_intelligence.models.news import NewsCategory, NewsItem
from app.engines.news_intelligence.utils.time_utils import now_ist_iso


def check(condition, message):
    assert condition, f"FAILED: {message}"
    print(f"PASS: {message}")


def make_item(news_id, symbol="RELIANCE"):
    return NewsItem(
        news_id=news_id, headline=f"Headline {news_id}", summary="Summary",
        source="TEST", published_at=now_ist_iso(), fetched_at=now_ist_iso(),
        category=NewsCategory.CORPORATE_ANNOUNCEMENT, symbols=[symbol],
    )


def test_put_and_get():
    cache = NewsCache(ttl_seconds=60)
    item = make_item("n1")
    check(cache.put(item) is True, "first put() of a news_id succeeds")
    check(cache.get("n1").news_id == "n1", "get() returns the stored item")


def test_duplicate_put_returns_false():
    cache = NewsCache(ttl_seconds=60)
    item = make_item("n1")
    cache.put(item)
    check(cache.put(item) is False, "putting the same news_id twice returns False")
    check(cache.size() == 1, "duplicate put does not grow the cache")


def test_ttl_expiry():
    cache = NewsCache(ttl_seconds=0.2)
    cache.put(make_item("n1"))
    check(cache.get("n1") is not None, "item is retrievable immediately after put")
    time.sleep(0.3)
    check(cache.get("n1") is None, "item is gone after its TTL expires")


def test_get_by_symbol():
    cache = NewsCache(ttl_seconds=60)
    cache.put(make_item("n1", symbol="TCS"))
    cache.put(make_item("n2", symbol="TCS"))
    cache.put(make_item("n3", symbol="INFY"))
    tcs_items = cache.get_by_symbol("TCS")
    check(len(tcs_items) == 2, "get_by_symbol returns only items mapped to that symbol")


def test_max_items_eviction():
    cache = NewsCache(ttl_seconds=60, max_items=3)
    for i in range(5):
        cache.put(make_item(f"n{i}"))
    check(cache.size() == 3, "cache never exceeds max_items (oldest evicted first)")
    check(cache.get("n0") is None, "oldest item was evicted first (FIFO)")
    check(cache.get("n4") is not None, "most recent item survives eviction")


def test_thread_safety_concurrent_puts():
    cache = NewsCache(ttl_seconds=60, max_items=10000)
    errors = []

    def worker(start):
        try:
            for i in range(start, start + 200):
                cache.put(make_item(f"n{i}"))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(t * 200,)) for t in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    check(not errors, "concurrent puts from 5 threads raise no exceptions")
    check(cache.size() == 1000, "all 1000 concurrently-put unique items are present, no lost updates")


def test_clear():
    cache = NewsCache(ttl_seconds=60)
    cache.put(make_item("n1"))
    cache.clear()
    check(cache.size() == 0, "clear() empties the cache")


if __name__ == "__main__":
    tests = [obj for name, obj in list(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
    print(f"\n{len(tests)} test functions passed.")
