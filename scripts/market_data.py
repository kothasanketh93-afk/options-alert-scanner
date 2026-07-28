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

_broker is a FailoverBroker (Upstox primary, Angel One fallback, as of
2026-07-28 -- see history below) -- see broker_failover.py's module
docstring for the failover design. Everything in this file that calls
_broker.* automatically gets failover for free, since FailoverBroker
implements the same Broker interface as any single concrete broker.

History of which broker is primary, so the next person editing this
doesn't re-discover it the hard way:
  - Originally Angel One primary.
  - SWAPPED 2026-07-21: Angel One's getCandleData (historical candle)
    endpoint hit a sustained rate limit ("Access denied because of
    exceeding access rate") that persisted across retries-with-backoff
    AND a 1-second per-call throttle -- notably on the very FIRST
    Angel One call of a fresh process, ruling out this process's own
    call pacing as the sole cause. Root cause undetermined (possibly
    an account-wide limit Angel One doesn't publish). Upstox served
    every request reliably throughout that debugging session, so it
    became primary.
  - SWAPPED BACK 2026-07-23: per explicit request, AND because the
    throttle itself has since been strengthened -- it was previously
    fixed-interval-only (guaranteed spacing between consecutive calls
    but never checked a rolling total), and is now a genuine
    rolling-window limiter enforcing BOTH the 1-second spacing and a
    real 60-second-window cap (config.ANGELONE_HISTORICAL_MAX_CALLS_PER_MINUTE,
    see angelone.py's _throttle_historical_call()) -- modeled on
    OpenAlgo's (github.com/marketcalls/openalgo) own fix for the same
    class of problem. This was NOT a confirmed fix -- Angel One's own
    SmartAPI forum separately admitted their rate-limit enforcement was
    inconsistent even for well-under-limit clients.
  - SWAPPED BACK 2026-07-28 (explicit request) -- Upstox primary again,
    Angel One fallback. The 2026-07-23 throttle strengthening above did
    NOT hold: live CI logs from 2026-07-28 show getCandleData hit
    "400 rate limited by
    https://apiconnect.angelone.in/rest/secure/angelbroking/historical/v1/getCandleData"
    on the run's very first two consecutive Angel One calls again,
    tripping FAILOVER_CONSECUTIVE_FAILURE_THRESHOLD and switching to
    Upstox mid-run anyway -- so Upstox was already doing the real work
    that run, just one step later and one BROKER SWITCH log line later
    than necessary. Making it primary removes that guaranteed first-call
    failure + switch on every run instead of only the runs where Angel
    One happens to recover. If Upstox itself starts showing sustained
    failures instead, swap the constructor args below back to Angel One
    primary -- same one-line change either direction, no other file
    needs to know or care which broker is active (see FailoverBroker's
    own docstring in broker_failover.py).
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
# (Upstox, as of 2026-07-28 -- see module docstring above) every run.
_angelone = AngelOneBroker()
_upstox = UpstoxBroker()
_broker = FailoverBroker(_upstox, "Upstox", _angelone, "AngelOne")

# Underlying-index candles (NOT option candles) are frequently requested
# more than once per run for the identical (instrument_key, interval)
# pair -- e.g. scanner.py's market_regime() and multi_timeframe_confirms()
# both fetch the same 15-min underlying candles independently. A TTL
# comfortably longer than a single run's wall-clock time (see trader.yml's
# timeout-minutes for the current run budget) means every call after the
# first one within a run is served from memory instead of hitting the
# broker again.
_underlying_candle_cache = TTLCache(ttl_seconds=180)

# NEGATIVE cache, companion to _underlying_candle_cache above (2026-07-28,
# live CI: same run's scanner.py AND early_breakout engine both call
# get_recent_underlying_candles() for the same (instrument_key, interval)
# -- when the broker call fails (e.g. Angel One rate-limited), nothing
# gets stored in _underlying_candle_cache (only successes are), so the
# SECOND caller re-attempts the exact same already-failing broker call,
# burning another RETRY_MAX_ATTEMPTS worth of retries against an
# endpoint that's already known-bad for this run. This remembers a
# recent failure for a short TTL (shorter than _underlying_candle_cache's,
# since a broker/rate-limit issue can clear faster than the successful-
# data TTL implies) and re-raises the SAME exception immediately instead
# of re-hitting the broker -- saves both wall-clock time and API-call
# budget on an endpoint already under strain.
_underlying_candle_failure_cache = TTLCache(ttl_seconds=60)

# BUGFIX (2026-07-23, live perf issue): scanner.score_strike() was
# calling market_data.get_broker().get_option_chain() directly, once
# PER STRIKE scored -- up to 3 moneyness buckets x up to 2 directions
# per symbol, all for the exact same (underlying_key, expiry) chain,
# which doesn't change within one run. That's up to 6x redundant
# full-chain refetches per symbol, x ~35 symbols in the shortlist --
# almost certainly the dominant cost behind runs taking ~2 minutes
# during market hours (alerts arriving late enough to miss the entry
# window). Same fix already applied to VWAP (_vwap_cache below) and
# underlying candles (_underlying_candle_cache above) -- this call
# site was just missed. Keyed by (underlying_key, expiry_date); same
# 180s TTL rationale as _underlying_candle_cache above.
_option_chain_cache = TTLCache(ttl_seconds=180)


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

    A recent failure for this (instrument_key, interval) is also
    remembered briefly (see _underlying_candle_failure_cache above) --
    a second caller within that window gets the same exception
    re-raised immediately instead of re-hitting an already-failing
    broker call.
    """
    cache_key = (instrument_key, interval)
    candles = _underlying_candle_cache.get(cache_key)
    if candles is None:
        cached_failure = _underlying_candle_failure_cache.get(cache_key)
        if cached_failure is not None:
            raise cached_failure
        try:
            candles = _broker.get_candles(instrument_key, interval)
        except Exception as exc:
            _underlying_candle_failure_cache.set(cache_key, exc)
            raise
        _underlying_candle_cache.set(cache_key, candles)
    return candles[-count:] if len(candles) > count else candles


def get_option_chain(underlying_key: str, expiry_date: str) -> list:
    """
    Fetches the full option chain for one (underlying_key, expiry_date)
    -- one row per strike, with instrument_key/oi/volume/bid/ask/delta
    per leg. Cached per (underlying_key, expiry_date) for this run: the
    chain doesn't meaningfully change across the several strikes
    scanner.score_strike() scores for the same symbol within one ~5-min
    cycle, so every call after the first for a given symbol+expiry is
    served from memory instead of re-hitting the broker. See
    _option_chain_cache above for the incident this fixes.
    """
    cache_key = (underlying_key, expiry_date)
    chain = _option_chain_cache.get(cache_key)
    if chain is None:
        chain = _broker.get_option_chain(underlying_key, expiry_date)
        _option_chain_cache.set(cache_key, chain)
    return chain


def get_recent_futures_candles(symbol: str, interval: str, count: int) -> list:
    """
    BUGFIX (2026-07-22): companion to get_recent_underlying_candles()
    above, but specifically for the current-month FUTURES contract,
    which -- unlike an index's fixed instrument_key -- must be resolved
    fresh via resolve_current_month_future() first. That resolution and
    the candle fetch used to be two independent calls through
    market_data.get_broker() (in option_chain.py's _futures_vwap()),
    which could resolve the key via one broker (on a single-call
    failover fallback) and then fetch candles for it via the OTHER
    broker, since each call independently checks which broker is
    currently active. Instrument keys are broker-specific format, so
    that mismatch produced a guaranteed-invalid request and a live 400
    error on every affected stock -- see
    broker_failover.FailoverBroker.resolve_and_fetch_current_month_future_candles()
    for the full incident writeup. That method now guarantees resolve +
    fetch happen against the SAME broker.

    Cached by (symbol, interval) rather than (instrument_key, interval)
    -- the resolved instrument_key can legitimately differ between
    calls within one run if a failover happens mid-run, so caching on
    the broker-agnostic symbol is what's actually stable here.
    """
    cache_key = (symbol, interval, "futures")
    candles = _underlying_candle_cache.get(cache_key)
    if candles is None:
        candles = _broker.resolve_and_fetch_current_month_future_candles(symbol, interval)
        _underlying_candle_cache.set(cache_key, candles)
    return candles[-count:] if len(candles) > count else candles


def get_recent_daily_candles(instrument_key: str, count: int) -> list:
    """
    Phase 1 (opportunity_score.py). Same shape and same per-run
    in-process caching approach as get_recent_underlying_candles()
    above, for DAILY bars instead of intraday ones -- see
    broker.Broker's get_daily_candles() documentation for why this
    needs a separate broker endpoint rather than a wider intraday
    window.

    Deliberately does NOT persist across runs the way
    instrument_master.py's cache does: daily bars for the scoring
    universe are only ever fetched once, in the single premarket run
    that computes today's opportunity shortlist -- there's no
    intraday re-fetch to save by persisting this across runs the way
    there is for the instrument master (which trader.yml's ~50 runs/day
    would otherwise each redownload).
    """
    cache_key = (instrument_key, "day")
    candles = _underlying_candle_cache.get(cache_key)
    if candles is None:
        candles = _broker.get_daily_candles(instrument_key, count)
        _underlying_candle_cache.set(cache_key, candles)
    return candles[-count:] if len(candles) > count else candles
