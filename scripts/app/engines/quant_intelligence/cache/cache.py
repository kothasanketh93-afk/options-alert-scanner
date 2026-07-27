"""
cache/cache.py -- Lightweight thread-safe TTL cache.

No external dependency (no functools.lru_cache misuse, no redis). Built
for the module's own hot paths: regime lookups, pattern bucket lookups,
and adaptive weight reads that don't need to hit disk on every one of
1000+ signals/day.

Safe to call from sync code and from async code via a thread pool --
the lock is a plain threading.Lock, held only for the O(1) dict
operations, never across I/O.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any, Optional, Tuple


class TTLCache:
    def __init__(self, max_entries: int = 5000, default_ttl_seconds: float = 30.0) -> None:
        self._max_entries = max_entries
        self._default_ttl = default_ttl_seconds
        self._store: "OrderedDict[str, Tuple[Any, float]]" = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Optional[Any]:
        now = time.monotonic()
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self.misses += 1
                return None
            value, expires_at = entry
            if expires_at < now:
                del self._store[key]
                self.misses += 1
                return None
            self._store.move_to_end(key)
            self.hits += 1
            return value

    def set(self, key: str, value: Any, ttl_seconds: Optional[float] = None) -> None:
        ttl = self._default_ttl if ttl_seconds is None else ttl_seconds
        expires_at = time.monotonic() + ttl
        with self._lock:
            self._store[key] = (value, expires_at)
            self._store.move_to_end(key)
            while len(self._store) > self._max_entries:
                self._store.popitem(last=False)  # evict oldest

    def delete(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def stats(self) -> dict:
        with self._lock:
            return {
                "size": len(self._store),
                "hits": self.hits,
                "misses": self.misses,
            }

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)
