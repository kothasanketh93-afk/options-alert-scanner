"""
production/performance.py
----------------------------
Part 9, item 10: Performance Optimization.

Not a rewrite of anything's algorithm -- three small, reusable
utilities that let existing (or new) code opt into the two concrete
performance requirements Part 9 asks for:

  1. "Avoid duplicate calculations / repeated API calls / reuse cached
     results" -> `memoize()`, a decorator wrapping
     production.cache_manager.ManagedCache (TTL + single-flight
     de-dup already built in Phase 4) around any function.
  2. "Improve startup speed / API response time / scanner execution" ->
     you can't improve what you don't measure -- `timed()` (decorator)
     and `Timer` (context manager) record wall-clock duration to the
     "performance" log channel (production.logging_manager) so slow
     spots show up in logs/performance.log instead of being guessed at.

Nothing existing has to change to get this -- these are opt-in
decorators/context managers for new code, or for wrapping an existing
slow call site with one line, not a rewrite of that call site's logic.
"""

from __future__ import annotations

import functools
import logging
import time
from typing import Any, Callable, Optional

from production.cache_manager import ManagedCache

try:
    from production.logging_manager import get_logger as _get_managed_logger
    _perf_logger = _get_managed_logger("performance")
except Exception:  # pragma: no cover - never let logging setup break perf measurement itself
    _perf_logger = logging.getLogger("performance")

# One shared cache for @memoize-decorated functions that don't supply
# their own -- fine for the common case (a handful of memoized
# functions in one process); pass cache=ManagedCache(...) to
# memoize() for isolated sizing/TTL per function if needed.
_default_memo_cache = ManagedCache(max_entries=2000, default_ttl_seconds=60.0)


def memoize(
    ttl_seconds: float = 60.0,
    cache: Optional[ManagedCache] = None,
    key_fn: Optional[Callable[..., str]] = None,
):
    """Caches a function's return value keyed on its arguments.
    Single-flight: concurrent calls with the same arguments compute
    once (see ManagedCache.get_or_load). Only use on pure-ish
    functions -- arguments must be representable in the cache key
    (default key_fn uses repr(args)+repr(kwargs), which is wrong for
    e.g. a live DB connection object; supply key_fn for those cases).

    Example:
        @memoize(ttl_seconds=30)
        def get_option_chain(symbol):
            return upstox.fetch_option_chain(symbol)   # expensive
    """
    target_cache = cache or _default_memo_cache

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            if key_fn is not None:
                key = key_fn(*args, **kwargs)
            else:
                key = f"{fn.__module__}.{fn.__qualname__}:{args!r}:{sorted(kwargs.items())!r}"
            return target_cache.get_or_load(key, lambda: fn(*args, **kwargs), ttl_seconds=ttl_seconds)
        wrapper.cache = target_cache  # exposes .stats() / .invalidate_pattern() on the wrapped fn
        return wrapper
    return decorator


def timed(label: Optional[str] = None, warn_above_seconds: Optional[float] = None):
    """Decorator: logs how long a function took to the "performance"
    channel every call. If warn_above_seconds is given, logs at
    WARNING instead of DEBUG when exceeded -- e.g. flag any scan cycle
    over 5s instead of scrolling through every cycle's timing by hand.
    """
    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        name = label or f"{fn.__module__}.{fn.__qualname__}"

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            start = time.perf_counter()
            try:
                return fn(*args, **kwargs)
            finally:
                elapsed = time.perf_counter() - start
                if warn_above_seconds is not None and elapsed > warn_above_seconds:
                    _perf_logger.warning("%s took %.3fs (over %.3fs threshold)", name, elapsed, warn_above_seconds)
                else:
                    _perf_logger.debug("%s took %.3fs", name, elapsed)
        return wrapper
    return decorator


class Timer:
    """Context-manager form of `timed()`, for timing a block rather
    than a whole function:

        with Timer("replay_load"):
            replay = load_replay_journal(date)

    `elapsed_seconds` is available on the instance after the `with`
    block exits, for callers that also want the number (e.g. to feed
    into a resource_monitor gauge), not just the log line.
    """

    def __init__(self, label: str, warn_above_seconds: Optional[float] = None):
        self.label = label
        self.warn_above_seconds = warn_above_seconds
        self.elapsed_seconds: Optional[float] = None
        self._start: Optional[float] = None

    def __enter__(self) -> "Timer":
        self._start = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.elapsed_seconds = time.perf_counter() - self._start
        if self.warn_above_seconds is not None and self.elapsed_seconds > self.warn_above_seconds:
            _perf_logger.warning(
                "%s took %.3fs (over %.3fs threshold)", self.label, self.elapsed_seconds, self.warn_above_seconds,
            )
        else:
            _perf_logger.debug("%s took %.3fs", self.label, self.elapsed_seconds)
