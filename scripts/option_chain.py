"""
option_chain.py -- Broker/option-chain interaction, split out of scanner.py.
------------------------------------------------------------------------------
Everything here talks to market_data / the broker: instrument keys,
expiry resolution, ATM strike, per-leg extraction from the option
chain, VWAP-on-futures check, and multi-timeframe / directional bias.
"""

from datetime import datetime, timezone, timedelta

import market_data
from config import STRIKE_STEP, STOCK_SYMBOLS
from indicators import ema_trend_direction, compute_vwap, vwap_confirms
from logging_setup import get_logger
from exceptions import ConfigurationError

IST = timezone(timedelta(hours=5, minutes=30))
_logger = get_logger("option_chain")

# Environment/account-specific instrument identifiers -- NOT thresholds,
# so these stay here next to the code that uses them rather than in
# config.py (see config.py's docstring for the reasoning).
UNDERLYING_INSTRUMENT_KEYS = {
    # Verified against Upstox's documented instrument-key examples
    # (option chain / market-quote docs, July 2026) -- these are the
    # standard NSE index keys and don't change; no expiry/rollover
    # concerns here since these point at the index itself, not a
    # derivative contract (that's what VWAP's futures-contract lookup
    # in vwap_check() is for).
    "NIFTY": "NSE_INDEX|Nifty 50",
    "BANKNIFTY": "NSE_INDEX|Nifty Bank",
    # Added 2026-07-17. Upstox's own site displays this index as
    # "NIFTY MID SELECT" (upstox.com/indices/nifty-mid-select-...),
    # matching their NSE_INDEX|<display name> pattern used for the two
    # keys above -- NOT verified against a downloaded instrument file
    # the way NIFTY/BANKNIFTY's keys were. Confirm this resolves (no
    # InstrumentNotFound) on the first live MIDCPNIFTY scan.
    "MIDCPNIFTY": "NSE_INDEX|NIFTY MID SELECT",
}

# IMPORTANT: the raw index (NSE_INDEX|...) has no genuine traded
# volume -- it's a calculated value, not something bought/sold -- so
# every candle's volume reads 0 and compute_vwap() would divide 0/0
# and return None forever, silently failing every VWAP check even
# after volume is correctly plumbed through market_data.py. VWAP needs
# an instrument that's ACTUALLY traded: the current-month futures
# contract. This used to be a static VWAP_INSTRUMENT_KEYS dict that
# needed a manual update every month at rollover -- removed in favor
# of vwap_check() below resolving the current-month contract fresh
# each call via the broker's instrument master, which auto-rolls
# forward every month forever with no code changes ever needed again.


def resolve_underlying_key(symbol: str) -> str:
    """
    Single place every function below goes through to get `symbol`'s
    underlying instrument_key -- indices resolve instantly from the
    static UNDERLYING_INSTRUMENT_KEYS dict above (their NSE_INDEX|...
    key never changes). Individual stocks (config.STOCK_SYMBOLS) have
    no equivalent stable key to hand-type here -- an equity's
    instrument_key is tied to its ISIN, which isn't something to
    copy into source and trust -- so instead it's resolved fresh each
    call via Broker.resolve_equity_instrument_key(symbol) (see
    upstox.py), the same "resolve fresh from the instrument master,
    don't hardcode" approach vwap_check() below already uses for the
    futures contract key.

    Raises NotImplementedError if `symbol` is neither a known index
    nor in STOCK_SYMBOLS -- same failure mode the three functions
    below used to raise individually before this was centralized.
    """
    underlying_key = UNDERLYING_INSTRUMENT_KEYS.get(symbol)
    if underlying_key:
        return underlying_key
    if symbol in STOCK_SYMBOLS:
        return market_data.get_broker().resolve_equity_instrument_key(symbol)
    raise NotImplementedError(
        f"No underlying instrument key resolution configured for '{symbol}' "
        f"-- add it to UNDERLYING_INSTRUMENT_KEYS (index) or STOCK_SYMBOLS "
        f"(stock) in config.py."
    )


# Per-PROCESS cache only (one GitHub Actions run = one process): a
# single scan cycle calls vwap_check()/vwap_distance_pct() once per
# strike evaluated (up to 3 moneyness buckets x up to 2 directions per
# symbol), which used to mean up to 6 redundant futures-VWAP
# refetches per symbol per run for what's the same VWAP reading the
# whole cycle. Keyed by symbol only -- VWAP over a 75-candle 5-min
# window doesn't meaningfully change within one ~5-minute run cycle,
# and every run is a fresh process anyway, so there's no staleness
# risk carried across runs.
_vwap_cache = {}


def _futures_vwap(symbol: str):
    if symbol in _vwap_cache:
        return _vwap_cache[symbol]
    # BUGFIX (2026-07-22): this used to be two independent calls --
    # market_data.get_broker().resolve_current_month_future(symbol) then
    # market_data.get_recent_underlying_candles(futures_key, ...) -- each
    # of which independently picks primary vs. secondary broker at the
    # moment it runs. A resolve-time single-call failover fallback could
    # return an instrument_key in the SECONDARY broker's format, which
    # the very next (still-primary-routed) candle fetch then sent to the
    # PRIMARY broker's API -- a guaranteed-invalid request (see
    # broker_failover.FailoverBroker.resolve_and_fetch_current_month_future_candles()
    # for the full incident writeup). get_recent_futures_candles() below
    # keeps resolution + fetch as one atomic broker choice.
    candles = market_data.get_recent_futures_candles(symbol, "5minute", 75)
    vwap = compute_vwap(candles)
    _vwap_cache[symbol] = vwap
    return vwap


def vwap_check(symbol: str, spot_price: float, direction: str) -> bool:
    """
    Fetches VWAP from the current-month FUTURES contract (not the
    index -- see the comment above UNDERLYING_INSTRUMENT_KEYS for why)
    and checks it against `direction`. Call this from score_strike()
    instead of wiring compute_vwap()/vwap_confirms() together
    yourself, so the "use futures, not index" requirement can't be
    silently skipped.

    The futures instrument_key is resolved fresh each call via
    market_data.get_broker().resolve_current_month_future(symbol) --
    this rolls forward automatically every month, forever, with no
    manual key update ever needed again (see upstox.py's docstring on
    that method). Any exception it raises (InstrumentNotFound,
    BrokerAPIError, ConfigurationError) propagates from here loudly,
    same as the old NotImplementedError did -- better to know
    immediately than to have every trade quietly rejected with no
    explanation.
    """
    vwap = _futures_vwap(symbol)
    return vwap_confirms(spot_price, vwap, direction)


def vwap_distance_pct(symbol: str, spot_price: float, direction: str):
    """
    Phase 3 (trade_score.py): SIGNED distance of spot from futures
    VWAP, as a fraction of VWAP, in the direction that FAVORS the
    trade -- positive means "confirms and by how much", negative means
    "against, even if vwap_check() already gated this out upstream".
    Returns None if VWAP is unavailable (same fail-closed posture as
    vwap_confirms() -- the caller's continuous-strength math should
    treat None as zero strength, not guess).
    """
    vwap = _futures_vwap(symbol)
    if vwap is None or not vwap:
        return None
    raw_pct = (spot_price - vwap) / vwap
    return raw_pct if direction == "CE" else -raw_pct


def multi_timeframe_confirms(symbol: str, candidate_direction: str) -> bool:
    underlying_key = resolve_underlying_key(symbol)
    candles_15m = market_data.get_recent_underlying_candles(underlying_key, "15minute", 30)
    return ema_trend_direction(candles_15m) == candidate_direction


def directional_bias(symbol: str) -> list:
    """
    5-min EMA9/21 crossover as the primary directional signal,
    confirmed by 15-min agreement via multi_timeframe_confirms() --
    this is the minimum confluence bar (2 independent timeframes
    agreeing) the module docstring's "confluence principle" asks for.
    Volume/breakout confirmation is layered on top inside score_strike()
    (volume_confirmed factor), not here -- this function only decides
    WHICH direction(s), if any, are even worth scoring strikes for.

    NOTE: this is a reasonable default, not necessarily YOUR existing
    strategy. If your current scanner uses a different primary signal
    (e.g. price breakout above/below a range, not an EMA cross), swap
    the 5-min leg below for that logic and leave the 15-min
    confirmation call as-is.

    Returns ["CE"], ["PE"], both (rare -- would mean the two
    timeframes disagree with each other in a way that shouldn't
    normally produce both), or [] if no bias / insufficient data.
    """
    underlying_key = resolve_underlying_key(symbol)

    candles_5m = market_data.get_recent_underlying_candles(underlying_key, "5minute", 30)
    bias_5m = ema_trend_direction(candles_5m)
    if bias_5m is None:
        return []  # flat/insufficient 5-min data -- nothing to act on

    if not multi_timeframe_confirms(symbol, bias_5m):
        return []  # 15-min trend disagrees -- fails the confluence bar

    return [bias_5m]


def get_atm_strike(symbol: str):
    """
    Fetches the current spot price for `symbol`'s underlying index via
    the broker's LTP endpoint (market_data.get_broker().get_ltp(),
    which gets failover for free the same as every other broker call
    in this module).

    Phase 2 (dynamic strike selection): the ATM strike itself is now
    resolved from instrument_master.py's real listed strikes for
    today's nearest expiry -- nearest-value lookup into the actual
    sorted strike array, not `round(spot / STRIKE_STEP[symbol]) *
    STRIKE_STEP[symbol]`. That arithmetic assumed evenly-spaced
    strikes starting from 0, which holds for NIFTY/BANKNIFTY but isn't
    reliably true for individual stock options (see config.py's own
    "unverified"/"a guess" comments on STOCK_STRIKE_STEP).

    FALLBACK: if instrument_master has no data yet for this symbol
    today (premarket.yml's cache step hasn't run, or any lookup
    error), this degrades to the exact pre-Phase-2 fixed-step
    arithmetic rather than raising -- a bad/missing cache should never
    be the reason a symbol gets skipped outright when the old
    approach would have worked fine.

    Returns (atm_strike, spot_price).
    """
    underlying_key = resolve_underlying_key(symbol)
    spot_price = market_data.get_broker().get_ltp(underlying_key)

    try:
        import instrument_master
        expiry = current_expiry(symbol)
        strikes = instrument_master.strikes_for(symbol, expiry, instrument_master.today_str())
        if strikes:
            nearest = min(strikes, key=lambda s: abs(s - spot_price))
            return nearest, spot_price
    except Exception as exc:
        _logger.warning(
            f"STRIKE_ENGINE {symbol}: instrument_master ATM lookup failed "
            f"({type(exc).__name__}: {exc}) -- falling back to fixed STRIKE_STEP arithmetic"
        )

    step = STRIKE_STEP.get(symbol)
    if step is None:
        # Phase 1 (opportunity_score.py) can shortlist symbols that
        # were never in the old hardcoded STOCK_STRIKE_STEP 15-stock
        # dict -- instrument_master (above) is the primary path for
        # ALL of them now, so this only bites when that ALSO had no
        # data. No safe arithmetic step exists for a symbol with
        # neither -- raising here (instead of guessing a step) is
        # deliberate: detect_all_setups()'s per-symbol try/except
        # already turns this into a clean skip-and-log for this one
        # symbol, same as any other per-symbol failure.
        raise ConfigurationError(
            f"{symbol}: no instrument_master strikes AND no STRIKE_STEP fallback "
            f"entry -- cannot resolve an ATM strike for this symbol today."
        )
    atm_strike = round(spot_price / step) * step
    return atm_strike, spot_price


def current_expiry(symbol: str) -> str:
    """
    Returns the nearest unexpired contract's expiry date ('YYYY-MM-DD')
    via Broker.get_current_expiry(symbol) -- now implemented in
    broker.py (abstract interface), upstox.py (GET /v2/option/contract),
    angelone.py (resolved from the instrument master, converted from
    its native "DDMMMYYYY" format to ISO), and broker_failover.py
    (pass-through, same count_failures=False treatment as
    search_instrument()/resolve_current_month_future() -- an
    expiry-list lookup is data-specific, not a primary-health signal).

    IMPORTANT -- BANKNIFTY weekly expiry: NSE restricted weekly index
    options to NIFTY 50 only from the Sept-2025 restructuring: BANKNIFTY
    is monthly-only (last Tuesday) now. Both broker implementations
    return whatever the nearest listed expiry actually is, so this is
    handled correctly as long as the instrument master / option-contract
    list itself is current -- just don't assume BANKNIFTY has a weekly
    series when reasoning about expiry_theta_ok()'s
    EXPIRY_DAY_THRESHOLD_DAYS window.
    """
    return market_data.get_broker().get_current_expiry(symbol)


def days_to_expiry(expiry_date_str: str, now: datetime = None) -> int:
    """expiry_date_str: 'YYYY-MM-DD'. Returns 0 on expiry day itself."""
    now = now or datetime.now(IST)
    expiry_date = datetime.strptime(expiry_date_str, "%Y-%m-%d").date()
    return (expiry_date - now.date()).days


def _extract_leg(chain: list, strike: float, direction: str) -> dict:
    """
    Checked against the ACTUAL get_option_chain() implementations in
    upstox.py and angelone.py (not the raw Upstox API response) --
    both already normalize to the SAME flat, per-strike shape before
    returning: one dict per strike with "strike_price" plus
    call_/put_-prefixed keys (call_instrument_key, call_ltp, call_oi,
    call_volume, call_bid, call_ask, call_delta, and the put_
    equivalents). upstox.py's get_option_chain() docstring explains why
    it flattens Upstox's actually-nested raw API response into this
    shape: so score_strike() can read either broker's chain
    identically, including across a mid-day Angel One <-> Upstox
    failover.

    (An earlier version of this function assumed the raw NESTED
    Upstox shape -- "call_options"/"put_options" sub-dicts with
    "market_data"/"option_greeks" -- which is what Upstox's endpoint
    itself returns, but not what get_option_chain() hands back after
    its own flattening. That mismatch would have made every strike
    lookup silently return None here, since chain rows never actually
    contain a "call_options"/"put_options" key.)

    Angel One has NO single option-chain endpoint (confirmed via their
    own developer forum -- "We do not provide option chain data as of
    now"); angelone.py's get_option_chain() assembles this same flat
    shape from 3 separate calls (Option Greeks API for delta, a batch
    quote call for oi/volume/bid/ask/ltp) -- see that function's
    docstring. See the flag below this function about a reported Angel
    One Greeks-API bug worth testing for specifically.
    """
    prefix = "call_" if direction == "CE" else "put_"
    for row in chain:
        if row.get("strike_price") == strike:
            instrument_key = row.get(prefix + "instrument_key")
            if instrument_key is None:
                return None  # this side isn't listed for this strike -- not a gate failure, just absent
            return {
                "instrument_key": instrument_key,
                "ltp": row.get(prefix + "ltp"),
                "oi": row.get(prefix + "oi"),
                "volume": row.get(prefix + "volume"),
                "bid": row.get(prefix + "bid"),
                "ask": row.get(prefix + "ask"),
                "delta": row.get(prefix + "delta"),
            }
    return None


# *** FLAG THIS FOR TESTING, DON'T JUST TRUST IT ***
# Angel One's own developer forum has an open, unresolved report of
# their Option Greeks API returning the MONTHLY contract's delta when
# a WEEKLY expiry was explicitly requested (same strike). If
# angelone.py's get_option_chain() sources delta from that endpoint,
# a wrong delta silently corrupts dynamic_trade_levels() (SL/target
# sizing) and delta_acceptable()/expiry_theta_ok() (the OTM lottery-
# ticket filters) with no error thrown anywhere. Before trusting this
# live: pull one known weekly-expiry strike's delta from Angel One and
# sanity-check it against the same strike on Upstox (or another
# source) on a day when weekly and monthly deltas would visibly
# differ (e.g. a strike moderately away from spot, a few days before
# weekly expiry).