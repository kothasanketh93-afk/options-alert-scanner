"""
cache.py
--------
Tiny in-process TTL cache. Deliberately NOT persisted to disk or the
state branch: every GitHub Actions run starts from a fresh container,
so an on-disk cache would need its own git-branch storage and cleanup
(more moving parts, more failure modes) just to survive between runs
that are 5-15 minutes apart anyway. The actual goal here is avoiding
REPEAT calls within a single run -- e.g. the instrument master (a
large, identical-for-the-whole-run dataset) shouldn't be fetched once
per symbol per moneyness level when one fetch covers all of them.
"""

import time


class TTLCache:
    def __init__(self, ttl_seconds: float = 300):
        self._ttl = ttl_seconds
        self._store = {}

    def get(self, key):
        entry = self._store.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if time.time() > expires_at:
            del self._store[key]
            return None
        return value

    def set(self, key, value):
        self._store[key] = (value, time.time() + self._ttl)

    def get_or_set(self, key, compute_fn):
        cached = self.get(key)
        if cached is not None:
            return cached
        value = compute_fn()
        self.set(key, value)
        return value
