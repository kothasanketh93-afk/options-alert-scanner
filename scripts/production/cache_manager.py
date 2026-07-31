"""
production/cache_manager.py
------------------------------
Part 9, item 9: Cache Improvements.

Relationship to the existing scripts/cache.py and scripts/daily_cache.py
------------------------------------------------------------------------
Neither existing cache is touched or replaced:

- cache.py's TTLCache is still exactly what upstox.py/angelone.py use
  for their own in-process, single-run instrument-master memoization.
- daily_cache.py's git-branch-backed persistence is still exactly what
  instrument_master.py/opportunity_score.py use to survive between
  ephemeral GitHub Actions runs.

This module adds a third option, ManagedCache, for long-running
processes (the dashboard, a persistent worker) that need what
cache.TTLCache deliberately doesn't provide because it never needed to
in a short-lived process: a memory ceiling (LRU eviction), lazy
loading with single-flight de-duplication (two callers racing to
compute the same missing key compute it once, not twice), a
validation hook (reject/refresh a cached value that's gone stale by
some external signal, not just TTL), and pattern-based safe
invalidation. Existing call sites do not need to change.
"""

from __future__ import annotations

import fnmatch
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from exceptions import CacheError


@dataclass
class _Entry:
    value: Any
    expires_at: Optional[float]
    created_at: float


class ManagedCache:
    """LRU-bounded, TTL-expiring, thread-safe cache with lazy loading
    and pattern-based invalidation.

    max_entries bounds memory the simple way (entry count, not a
    memory-profiler-accurate byte count) -- accurate enough to stop
    unbounded growth without pulling in a sizing dependency, and
    consistent with this codebase's existing preference for the
    simplest mechanism that solves the actual problem (see cache.py's
    own docstring for the same reasoning applied to persistence).
    """

    def __init__(self, max_entries: int = 1000, default_ttl_seconds: float = 300.0):
        self.max_entries = max_entries
        self.default_ttl_seconds = default_ttl_seconds
        self._store: "OrderedDict[str, _Entry]" = OrderedDict()
        self._lock = threading.RLock()
        self._inflight: Dict[str, threading.Event] = {}
        self._inflight_results: Dict[str, Any] = {}
        self._inflight_errors: Dict[str, BaseException] = {}
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    def _is_expired(self, entry: _Entry) -> bool:
        return entry.expires_at is not None and time.time() > entry.expires_at

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self._misses += 1
                return None
            if self._is_expired(entry):
                del self._store[key]
                self._misses += 1
                return None
            self._store.move_to_end(key)  # LRU: mark as recently used
            self._hits += 1
            return entry.value

    def set(self, key: str, value: Any, ttl_seconds: Optional[float] = None) -> None:
        ttl = self.default_ttl_seconds if ttl_seconds is None else ttl_seconds
        expires_at = None if ttl is None or ttl <= 0 else time.time() + ttl
        with self._lock:
            self._store[key] = _Entry(value=value, expires_at=expires_at, created_at=time.time())
            self._store.move_to_end(key)
            while len(self._store) > self.max_entries:
                evicted_key, _ = self._store.popitem(last=False)  # evict least-recently-used
                self._evictions += 1

    def delete(self, key: str) -> bool:
        with self._lock:
            return self._store.pop(key, None) is not None

    def invalidate_pattern(self, pattern: str) -> int:
        """Safe invalidation: removes every key matching a glob-style
        pattern (e.g. "option_chain:NIFTY:*") rather than requiring
        the caller to enumerate exact keys, or clearing everything."""
        with self._lock:
            matching = [k for k in self._store if fnmatch.fnmatch(k, pattern)]
            for k in matching:
                del self._store[k]
            return len(matching)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def cleanup_expired(self) -> int:
        """Proactively sweeps expired entries instead of waiting for a
        get() to discover them lazily -- call periodically from a
        background task (production.task_manager) to keep memory from
        holding dead entries in a low-traffic cache."""
        with self._lock:
            expired = [k for k, e in self._store.items() if self._is_expired(e)]
            for k in expired:
                del self._store[k]
            return len(expired)

    def get_or_load(
        self,
        key: str,
        loader: Callable[[], Any],
        ttl_seconds: Optional[float] = None,
        validate: Optional[Callable[[Any], bool]] = None,
    ) -> Any:
        """Lazy loading with single-flight de-duplication: if two
        threads call get_or_load() for the same missing key at the
        same time, only one actually calls `loader`; the other waits
        for and reuses its result instead of computing it twice.

        `validate`, if given, is called on a cache HIT before
        returning it; a validator returning False is treated as a
        miss (evicted, then reloaded) -- for staleness signals TTL
        alone can't express, e.g. "does this cached instrument key
        still exist in the latest instrument master."
        """
        cached = self.get(key)
        if cached is not None:
            try:
                still_valid = validate is None or self._safe_validate(validate, cached)
            except CacheError:
                still_valid = False  # a broken validator is treated as "can't confirm freshness" -> miss
            if still_valid:
                return cached
            self.delete(key)

        with self._lock:
            event = self._inflight.get(key)
            am_loader = event is None
            if am_loader:
                event = threading.Event()
                self._inflight[key] = event

        if not am_loader:
            event.wait()
            with self._lock:
                if key in self._inflight_errors:
                    raise self._inflight_errors.pop(key)
                if key in self._inflight_results:
                    return self._inflight_results.pop(key)
            # Fallback: the loader finished but something raced the
            # cleanup below -- just try the cache directly.
            return self.get(key)

        try:
            value = loader()
        except BaseException as exc:
            with self._lock:
                self._inflight_errors[key] = exc
                self._inflight.pop(key, None)
            event.set()
            raise
        else:
            self.set(key, value, ttl_seconds=ttl_seconds)
            with self._lock:
                self._inflight_results[key] = value
                self._inflight.pop(key, None)
            event.set()
            return value

    @staticmethod
    def _safe_validate(validate: Callable[[Any], bool], value: Any) -> bool:
        try:
            return bool(validate(value))
        except Exception as exc:  # noqa: BLE001 - a broken validator must not crash the cache
            raise CacheError(f"cache validator raised: {exc}") from exc

    def stats(self) -> Dict[str, int]:
        with self._lock:
            return {
                "entries": len(self._store),
                "hits": self._hits,
                "misses": self._misses,
                "evictions": self._evictions,
            }
