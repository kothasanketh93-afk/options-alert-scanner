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
2026-07-29 -- see history below) -- see broker_failover.py's module
docstring for the failover design. Everything in this file that calls
_broker.* automatically gets failover for free, since FailoverBroker
implements the same Broker interface as any single concrete broker.

AUTHORITATIVE SOURCE (verified, 2026-08-14 architecture cleanup audit):
Upstox is confirmed as this application's primary, authoritative
market-data source for every category this scanner uses -- spot,
candles, option chain, OI/ΔOI, Greeks/IV, and (as of this pass) India
VIX and native PCR, all via the SAME authenticated v2/v3 API this file
already calls. Repo-wide search this session confirmed: no
unofficial/scraped NSE option-chain source exists anywhere in this
codebase (only a legitimate, separate news/corporate-announcements
fetch in app/engines/news_intelligence/providers/nse_provider.py, not
a duplicate of price/OI data), and no yfinance/Yahoo Finance
dependency exists anywhere. AngelOne (see angelone.py) is the
FAILOVER, not a second independent source -- when it serves a request,
it's standing in for Upstox, not adding a genuinely separate opinion.

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
    than necessary.
  - BRIEFLY SWAPPED TO ANGEL ONE PRIMARY 2026-07-29, REVERTED SAME DAY:
    tried per explicit request; reverted after confirming (SmartAPI
    forum, 2026-07-29) that OTHER accounts unrelated to this project
    independently report the SAME "exceeding access rate" 403 while
    staying 3-4x UNDER Angel One's own published per-second limit --
    i.e. this is a server-side enforcement bug on Angel One's end
    (which their own team has acknowledged but not confirmed fixed),
    not something any client-side throttle change here can reliably
    work around. Decision: STAY on Upstox primary going forward --
    every one of the last two head-to-head incidents (2026-07-21,
    2026-07-28) resolved the same way, Upstox has served every request
    reliably both times, and this project places no orders (Telegram
    alerts only) and uses no Angel One capability Upstox lacks (candles,
    option chain, and delta/theta greeks are all available from Upstox
    too -- see upstox.py) -- so there is no functional reason for Angel
    One to ever be primary here. Angel One stays wired in as a genuine
    fallback for whatever a real Upstox outage might need. Do NOT swap
    this back to Angel One primary without a confirmation from Angel
    One support that the account/endpoint-level rate-limit enforcement
    bug referenced above has actually been fixed -- "the throttle looks
    more conservative now" is not that confirmation; both 2026-07-23
    and 2026-07-29 believed exactly that going in.
"""

from datetime import datetime, timezone, timedelta

from angelone import AngelOneBroker
from upstox import UpstoxBroker
from broker_failover import FailoverBroker
from cache import TTLCache
from logging_setup import get_logger
from config import (
    WARMUP_CANDLE_CONTINUITY_ENABLED, ATR_WARMUP_CUTOFF_HOUR, ATR_WARMUP_CUTOFF_MINUTE,
    ATR_WARMUP_TAIL_CANDLE_COUNT,
)

IST = timezone(timedelta(hours=5, minutes=30))
_logger = get_logger("market_data")

# One broker instance per process (= one per GitHub Actions run),
# shared across every call this run makes -- this is what makes
# angelone.py/upstox.py's instrument-master/option-chain caching
# actually useful (see cache.py's docstring for why that scope is the
# right one). FailoverBroker always starts fresh on "primary"
# (Upstox, as of 2026-07-29 -- see module docstring above) every run.
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

# FIX 2 (ATR warmup, 2026-07-30, config.WARMUP_CANDLE_CONTINUITY_ENABLED).
# Keyed by (instrument_key, interval) same as _underlying_candle_cache
# above -- the previous session's tail candles don't change during a
# run, so every call after the first for a given (instrument_key,
# interval) this run is served from memory. A longer TTL than
# _underlying_candle_cache's is fine (and harmless) since this data is
# from yesterday's session, not today's live candles -- but kept the
# same 180s for simplicity, since it's re-created fresh every process
# anyway (see FailoverBroker's per-run scope, this file's module
# docstring above).
_atr_warmup_cache = TTLCache(ttl_seconds=180)


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


def get_atr_warmup_candles(instrument_key: str, interval: str, today_candles: list,
                            min_candles: int = 15) -> tuple:
    """
    FIX 2 (ATR warmup, 2026-07-30) -- see config.WARMUP_CANDLE_CONTINUITY_ENABLED's
    docstring for the full problem statement: compute_atr() needs 15
    candles, but both brokers' get_candles() only ever return TODAY's
    candles, so on a 15-min interval ATR can't be ready until ~12:45
    IST.

    Returns (candles_for_indicators, combined_candle_count):
      - candles_for_indicators: `today_candles` UNCHANGED (byte-for-
        byte, same list) whenever:
          * config.WARMUP_CANDLE_CONTINUITY_ENABLED is False (default
            -- production behavior is identical to before this fix), OR
          * today's own candle count already reaches `min_candles`
            (DATA-based, not a wall-clock cutoff -- see this function's
            own 2026-08-20 BUGFIX comment below for why the previous
            fixed-11:00-IST cutoff was wrong, mirroring
            get_ema_warmup_candles()'s own 2026-08-11 fix), OR
          * the previous-session fetch raises, or comes back empty --
            fails OPEN to today-only candles rather than ever raising
            out of this function or blocking a scan over a warmup-only
            concern.
        Otherwise: previous-session tail candles PREPENDED in front of
        `today_candles`.
      - combined_candle_count: len(candles_for_indicators) -- purely
        for the diagnostic log line the caller prints; not itself used
        for any decision.

    CALLER CONTRACT: this return value is meant ONLY to feed
    compute_atr() (and, at the same call site, compute_ema()) --
    NEVER Opening Range, today's High/Low, Session VWAP, or breakout-
    level logic (find_nearby_level(), the early_breakout engine,
    app/market/snapshot.py). Those all call
    get_recent_underlying_candles() directly and never pass through
    this function, so they cannot see previous-session candles
    regardless of this flag.

    BUGFIX (2026-08-20, root-cause audit): this used to stop merging
    warmup candles purely at a fixed WALL-CLOCK cutoff
    (ATR_WARMUP_CUTOFF_HOUR:MINUTE, 11:00 IST) regardless of whether
    today's own candles had actually reached compute_atr()'s 15-candle
    requirement yet -- the EXACT same class of bug get_ema_warmup_
    candles() below was already fixed for on 2026-08-11 (see that
    function's own BUGFIX note), but the identical fix was never
    applied back to this function. Confirmed live: 9:15-11:00 IST is
    precisely 105 minutes = 7 fifteen-minute candles -- matching the
    exact "candles_fetched=7, atr_ready=False" symptom reported
    17-20 Aug across every run in that ~1h45m daily window (11:00
    until today-only data reaches 15 candles on its own, ~12:45 IST).
    Now DATA-based, same posture as get_ema_warmup_candles(): keep
    merging warmup candles for as long as today's own alone are still
    short of what's actually needed (period+1=15 for the default ATR
    period), regardless of the wall clock. Today-only is still always
    preferred once it can stand on its own.
    """
    if not WARMUP_CANDLE_CONTINUITY_ENABLED:
        return today_candles, len(today_candles)

    # requirement (period+1=15 for indicators.compute_atr()'s default
    # period=14 -- passed as `min_candles` default below, same "literal
    # default parameter, not a cross-module import" pattern
    # get_ema_warmup_candles() already uses for its own min_candles=21,
    # rather than importing compute_atr()'s period constant directly).
    if len(today_candles) >= min_candles:
        return today_candles, len(today_candles)

    cache_key = (instrument_key, interval)
    warmup_candles = _atr_warmup_cache.get(cache_key)
    if warmup_candles is None:
        try:
            warmup_candles = _broker.get_previous_session_candles(
                instrument_key, interval, ATR_WARMUP_TAIL_CANDLE_COUNT
            )
        except Exception as exc:
            _logger.warning(
                f"ATR_WARMUP: previous-session fetch failed for {instrument_key}/{interval} "
                f"({type(exc).__name__}: {exc}) -- falling back to today-only candles"
            )
            warmup_candles = []
        _atr_warmup_cache.set(cache_key, warmup_candles)

    if not warmup_candles:
        return today_candles, len(today_candles)

    combined = warmup_candles + today_candles
    return combined, len(combined)


def get_ema_warmup_candles(instrument_key: str, interval: str, today_candles: list,
                            min_candles: int = 21) -> tuple:
    """
    EMA-specific counterpart to get_atr_warmup_candles() above (2026-08-11)
    -- see that function's docstring for the shared previous-session-
    fetch mechanism this reuses (same _atr_warmup_cache entry, so when
    both this and get_atr_warmup_candles() are called for the same
    (instrument_key, interval) this cycle -- as scanner.py's 15-minute
    calls are -- only ONE broker call actually happens; whichever runs
    first populates the cache for the other).

    BUGFIX (2026-08-11): get_atr_warmup_candles() stops merging warmup
    candles at a fixed WALL-CLOCK cutoff (config.ATR_WARMUP_CUTOFF_HOUR/
    MINUTE, 11:00 IST) regardless of whether today's OWN candles have
    actually reached the requesting indicator's period yet. That's a
    reasonable trade-off for ATR (15-candle need, reached by ~12:45 IST
    on 15-min data -- see that function's own FIX-2 note) but wrong for
    EMA21: today-only 15-min data doesn't reach 21 candles until
    ~14:30 IST, so using the SAME 11:00 cutoff for EMA created a
    confirmed regression -- EMA ready (warmup-assisted) at 10:55 IST,
    then NOT ready again at 11:05 IST purely because the clock crossed
    11:00, then ready again only once today's own count finally reaches
    21 at ~14:30. This function's cutoff is DATA-based instead: it
    keeps merging warmup candles for as long as today's own candles
    alone are still short of `min_candles` (21, EMA21's requirement) --
    once today's own count reaches that on its own, warmup candles are
    dropped and today-only is used (today-only is always preferred once
    it can stand on its own, same posture get_atr_warmup_candles()
    already has for its own cutoff -- this just measures "enough" by
    data sufficiency instead of a fixed clock time).

    Returns (candles_for_ema, combined_candle_count) -- same shape as
    get_atr_warmup_candles().
    """
    if not WARMUP_CANDLE_CONTINUITY_ENABLED:
        return today_candles, len(today_candles)
    if len(today_candles) >= min_candles:
        return today_candles, len(today_candles)

    cache_key = (instrument_key, interval)
    warmup_candles = _atr_warmup_cache.get(cache_key)
    if warmup_candles is None:
        try:
            warmup_candles = _broker.get_previous_session_candles(
                instrument_key, interval, ATR_WARMUP_TAIL_CANDLE_COUNT
            )
        except Exception as exc:
            _logger.warning(
                f"EMA_WARMUP: previous-session fetch failed for {instrument_key}/{interval} "
                f"({type(exc).__name__}: {exc}) -- falling back to today-only candles"
            )
            warmup_candles = []
        _atr_warmup_cache.set(cache_key, warmup_candles)

    if not warmup_candles:
        return today_candles, len(today_candles)

    combined = warmup_candles + today_candles
    return combined, len(combined)


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


# --- Market context, logging-only (2026-08-14, architecture cleanup) ---
# India VIX -- deliberately NOT wired into scanner.py's scoring/gating
# at all. Fetched and logged ONCE per run (main.py calls this, not
# scanner.py's per-strike score_strike()) purely as market-regime
# context for a human reading the logs -- see this session's own
# repo audit for why nothing here should influence trade selection
# yet ("do not add new trading indicators", per the explicit scope of
# this pass). Instrument key confirmed against Upstox's own Full
# Market Quotes documentation (NSE_INDEX|India VIX) -- same
# resolution mechanism (Broker.get_ltp(), already implemented by both
# Upstox and AngelOne) as every other index price this module fetches,
# so this reuses existing, already-authenticated infrastructure
# rather than adding a new data source or new auth path.
INDIA_VIX_INSTRUMENT_KEY = "NSE_INDEX|India VIX"


def get_india_vix():
    """
    Returns the current India VIX level, or None if unavailable --
    NEVER raises. This is logging-only context (see module comment
    above): a failure here must never interrupt or degrade the actual
    scan, so every failure mode (network error, broker not supporting
    this instrument key, malformed response) is caught here and
    reported as None rather than propagated.
    """
    try:
        return _broker.get_ltp(INDIA_VIX_INSTRUMENT_KEY)
    except Exception as exc:
        _logger.warning(
            f"MARKET_CONTEXT_VIX: fetch failed ({type(exc).__name__}: {exc}) -- "
            f"logging-only context, does not affect scanning."
        )
        return None
