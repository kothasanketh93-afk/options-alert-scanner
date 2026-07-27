"""
app/engines/news_intelligence/cache/news_cache.py -- in-memory, TTL,
thread-safe news cache.
------------------------------------------------------------------------------
Backend is "memory" only (see config.cache.backend) -- deliberately no
Redis/DB dependency, matching the "no cloud dependency, lightweight"
production requirement. Safe for both a plain-threaded scheduler
(collector/scheduler.py's background thread) and asyncio callers (every
public method is a thin, fast, lock-guarded operation with no blocking
I/O inside the lock, so it's safe to call from a thread pool executor in
an async context without becoming an event-loop bottleneck).

Two lookup structures are kept in sync under one lock:
  _items: news_id -> (NewsItem, expires_at_epoch)
  _by_symbol: symbol -> set(news_id)   -- for get_by_symbol() without a
                                           full-table scan
"""

from __future__ import annotations

import threading
import time
from typing import Optional

from logging_setup import get_logger
from ..models.news import NewsItem

_logger = get_logger("news_intelligence")


class NewsCache:
    def __init__(self, ttl_seconds: int = 21600, max_items: int = 20000,
                 cleanup_interval_seconds: int = 300):
        self._ttl_seconds = ttl_seconds
        self._max_items = max_items
        self._cleanup_interval_seconds = cleanup_interval_seconds
        self._lock = threading.RLock()
        self._items: dict[str, tuple[NewsItem, float]] = {}
        self._by_symbol: dict[str, set[str]] = {}
        self._insertion_order: list[str] = []   # oldest-first, for max_items eviction
        self._last_cleanup = time.time()

    # --- write path ---------------------------------------------------

    def put(self, item: NewsItem, ttl_seconds: Optional[int] = None) -> bool:
        """Returns False if news_id already present (caller treats this
        as a duplicate -- see collector/deduplicator.py's exact-hash
        layer, which calls contains() first; put() itself also guards
        against a race between two collector threads)."""
        ttl = ttl_seconds if ttl_seconds is not None else self._ttl_seconds
        expires_at = time.time() + ttl
        with self._lock:
            self._maybe_cleanup_locked()
            if item.news_id in self._items:
                return False
            self._items[item.news_id] = (item, expires_at)
            self._insertion_order.append(item.news_id)
            for symbol in item.symbols:
                self._by_symbol.setdefault(symbol, set()).add(item.news_id)
            self._enforce_max_items_locked()
            return True

    # --- read path -------------------------------------------------------

    def get(self, news_id: str) -> Optional[NewsItem]:
        with self._lock:
            entry = self._items.get(news_id)
            if entry is None:
                return None
            item, expires_at = entry
            if expires_at < time.time():
                self._evict_locked(news_id)
                return None
            return item

    def contains(self, news_id: str) -> bool:
        return self.get(news_id) is not None

    def get_by_symbol(self, symbol: str, limit: int = 50) -> list[NewsItem]:
        with self._lock:
            ids = list(self._by_symbol.get(symbol, ()))
        results = []
        for news_id in ids:
            item = self.get(news_id)  # re-checks TTL, self-heals _by_symbol via _evict_locked
            if item is not None:
                results.append(item)
        results.sort(key=lambda it: it.published_at, reverse=True)
        return results[:limit]

    def all_active(self) -> list[NewsItem]:
        with self._lock:
            self._maybe_cleanup_locked()
            return [item for item, expires_at in self._items.values() if expires_at >= time.time()]

    def size(self) -> int:
        with self._lock:
            return len(self._items)

    # --- maintenance -------------------------------------------------

    def cleanup_expired(self) -> int:
        with self._lock:
            now = time.time()
            expired = [nid for nid, (_, exp) in self._items.items() if exp < now]
            for nid in expired:
                self._evict_locked(nid)
            self._last_cleanup = now
            if expired:
                _logger.info(f"NEWS_CACHE cleanup removed={len(expired)} remaining={len(self._items)}")
            return len(expired)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._by_symbol.clear()
            self._insertion_order.clear()

    # --- internals (caller must hold self._lock) ----------------------

    def _evict_locked(self, news_id: str) -> None:
        entry = self._items.pop(news_id, None)
        if entry is None:
            return
        item, _ = entry
        for symbol in item.symbols:
            bucket = self._by_symbol.get(symbol)
            if bucket:
                bucket.discard(news_id)
                if not bucket:
                    del self._by_symbol[symbol]
        if news_id in self._insertion_order:
            self._insertion_order.remove(news_id)

    def _maybe_cleanup_locked(self) -> None:
        if time.time() - self._last_cleanup >= self._cleanup_interval_seconds:
            now = time.time()
            expired = [nid for nid, (_, exp) in self._items.items() if exp < now]
            for nid in expired:
                self._evict_locked(nid)
            self._last_cleanup = now

    def _enforce_max_items_locked(self) -> None:
        while len(self._items) > self._max_items and self._insertion_order:
            oldest = self._insertion_order[0]
            self._evict_locked(oldest)


_default_cache: Optional[NewsCache] = None
_default_cache_lock = threading.Lock()


def get_default_cache() -> NewsCache:
    """Process-wide singleton cache, sized from config -- collector.py and
    engine.py share this instance unless a caller explicitly constructs
    its own NewsCache (tests do, for isolation)."""
    global _default_cache
    if _default_cache is None:
        with _default_cache_lock:
            if _default_cache is None:
                from ..config import get_settings

                cfg = get_settings().cache
                _default_cache = NewsCache(
                    ttl_seconds=cfg.ttl_seconds,
                    max_items=cfg.max_items,
                    cleanup_interval_seconds=cfg.cleanup_interval_seconds,
                )
    return _default_cache
