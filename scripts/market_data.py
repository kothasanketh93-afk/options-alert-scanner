"""
market_data.py
---------------
Thin compatibility layer over the Broker abstraction (broker.py /
angelone.py / upstox.py / broker_failover.py). This file's job is
exactly two function signatures -- get_1min_candles_since() and
get_recent_underlying_candles() -- kept byte-for-byte compatible with
what scanner.py, tracker.py, and main.py already call, so introducing
the broker abstraction (and later, failover) required ZERO changes to
those three files.

_broker is a FailoverBroker (Angel One primary, Upstox fallback) --
see broker_failover.py's module docstring for the failover design.
Everything in this file that calls _broker.* automatically gets
failover for free, since FailoverBroker implements the same Broker
interface as any single concrete broker.
"""

from angelone import AngelOneBroker
from upstox import UpstoxBroker
from broker_failover import FailoverBroker
from cache import TTLCache

# One broker instance per process (= one per GitHub Actions run),
# shared across every call this run makes -- this is what makes
# angelone.py/upstox.py's instrument-master/option-chain caching
# actually useful (see cache.py's docstring for why that scope is the
# right one). FailoverBroker always starts fresh on "primary"
# (Angel One) every run -- see its docstring for why that's also what
# gives you automatic restoration with no extra state to maintain.
_angelone = AngelOneBroker()
_upstox = UpstoxBroker()
_broker = FailoverBroker(_angelone, "AngelOne", _upstox, "Upstox")

# Underlying-index candles (NOT option candles) are frequently requested
# more than once per run for the identical (instrument_key, interval)
# pair -- e.g. scanner.py's market_regime() and multi_timeframe_confirms()
# both fetch the same 15-min underlying candles independently. A TTL
# comfortably longer than a single run's wall-clock time (see trader.yml's
# timeout-minutes for the current run budget) means every call after the
# first one within a run is served from memory instead of hitting the
# broker again.
_underlying_candle_cache = TTLCache(ttl_seconds=180)


def get_broker():
    """Returns the shared (failover-wrapped) Broker instance for this
    run -- what scanner.py/tracker.py should always use."""
    return _broker


def get_angelone_broker() -> AngelOneBroker:
    """The raw AngelOneBroker, unwrapped -- for checks that must
    specifically target Angel One regardless of which broker is
    currently active (e.g. heartbeat_check.py's session-health probe)."""
    return _angelone


def get_upstox_broker() -> UpstoxBroker:
    """The raw UpstoxBroker, unwrapped -- e.g. for
    auth.validate_upstox_token() in heartbeat_check.py, which must
    check Upstox's OWN token regardless of which broker is currently
    active."""
    return _upstox


def get_1min_candles_since(instrument_key: str, since_iso: str) -> list:
    """
    Fetches 1-minute candles for `instrument_key` from `since_iso`
    (exclusive) up to now, sorted ascending by timestamp. Used by
    tracker.py for one open trade's option instrument per call.

    Deliberately NOT cached: each call is for a distinct
    (instrument_key, since_iso) pair (a different open trade, or the
    same trade advancing in time), so there's no redundant fetch to
    remove, and caching a "since" query by its since value would be
    actively wrong (a later call with an earlier since_iso must not
    reuse a narrower cached result).
    """
    return _broker.get_candles(instrument_key, "1minute", since_iso=since_iso)


def get_recent_underlying_candles(instrument_key: str, interval: str, count: int) -> list:
    """
    Fetches the most recent `count` candles for the UNDERLYING index
    (NIFTY/BANKNIFTY spot or futures instrument_key, not an option) at
    the given interval. Used by scanner.py for multi-timeframe EMA
    confirmation, regime detection, and the mandatory VWAP filter.

    interval: "5minute" or "15minute".
    Returns ascending-sorted OHLCV candles, most recent `count` only.
    Each candle includes "volume" -- scanner.compute_vwap() requires
    it and fails closed (returns None -> vwap_confirms() rejects)
    without it.

    Cached per (instrument_key, interval) for this run -- see
    _underlying_candle_cache above. `count` is intentionally excluded
    from the cache key: the underlying fetch above always returns the
    full available history for the interval before slicing, so two
    calls with different `count` values for the same
    (instrument_key, interval) can safely share one cached fetch and
    just slice it differently.
    """
    cache_key = (instrument_key, interval)
    candles = _underlying_candle_cache.get(cache_key)
    if candles is None:
        candles = _broker.get_candles(instrument_key, interval)
        _underlying_candle_cache.set(cache_key, candles)
    return candles[-count:] if len(candles) > count else candles
