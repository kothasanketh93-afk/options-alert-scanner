"""
broker.py
---------
Abstract broker interface. scanner.py and tracker.py never import a
specific broker -- they go through market_data.py's thin compatibility
wrapper, which holds one Broker instance. Adding a second broker
(Angel One, mStock, Kite, Fyers) later means writing a new
`class SomeBroker(Broker)` in its own file and pointing
market_data.py's singleton at it -- not touching scanner.py or
tracker.py at all.

Every concrete broker must implement all four methods with exactly
these return shapes, so callers never need to know which broker is
actually configured.
"""

from abc import ABC, abstractmethod
from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))
INTERVAL_MINUTES = {"1minute": 1, "5minute": 5, "15minute": 15}


def drop_incomplete_trailing_candle(candles: list, interval: str, now: datetime = None) -> list:
    """
    Shared by every concrete Broker's get_candles() implementation --
    defined once here instead of duplicated per-broker so the fix
    can't drift between them.

    Broker candle-data APIs commonly include the currently-forming
    candle as the last element -- its high/low/close keep changing
    until the interval boundary actually passes. Using that unsettled
    candle is a real correctness risk in two places: tracker.py could
    flag a premature SL/T1/T2 hit off a high/low that hasn't finished
    forming yet, and scanner.py's EMA/ATR/VWAP readings would be
    computed against a partial bar. Only the LAST candle can possibly
    be incomplete -- every earlier one is, by definition, already
    fully elapsed -- so this only ever needs to check/drop one.

    Every concrete Broker.get_candles() implementation MUST call this
    on its result before returning, so this correctness guarantee
    holds no matter which broker is active (including mid-day
    failover between two brokers).

    Cost: up to one interval's worth of extra latency on the very
    latest reading (the next run picks it up once it's actually
    closed) -- same category of "notification delay is fine, don't
    use unsettled data" trade-off already made everywhere else in this
    project.
    """
    if not candles:
        return candles
    now = now or datetime.now(IST)
    interval_minutes = INTERVAL_MINUTES.get(interval)
    if interval_minutes is None:
        return candles  # unknown interval -- don't guess, leave as-is

    last = candles[-1]
    candle_start = datetime.fromisoformat(last["timestamp"])
    candle_close_time = candle_start + timedelta(minutes=interval_minutes)
    if now < candle_close_time:
        return candles[:-1]
    return candles


class Broker(ABC):
    @abstractmethod
    def get_candles(self, instrument_key: str, interval: str, since_iso: str = None) -> list:
        """
        Ascending-sorted OHLCV candles: a list of dicts with
        "timestamp" (ISO string), "open", "high", "low", "close",
        "volume" (all float except timestamp).

        interval: "1minute", "5minute", or "15minute".
        since_iso: if given, only candles strictly after this
        timestamp are returned (exclusive). If None, returns the full
        available intraday set for the instrument.

        REQUIRED: pass the result through drop_incomplete_trailing_candle()
        (defined in this module) before returning -- see its docstring
        for why. Every concrete implementation in this codebase does
        this; any new broker you add must too.
        """
        raise NotImplementedError

    @abstractmethod
    def get_ltp(self, instrument_key: str) -> float:
        """Last traded price for one instrument."""
        raise NotImplementedError

    @abstractmethod
    def get_option_chain(self, underlying_key: str, expiry_date: str) -> list:
        """
        Option chain for one expiry: a list of dicts, one per strike,
        each with at minimum strike_price, call/put instrument_key,
        oi, volume, bid, ask, and delta where the broker provides it.
        """
        raise NotImplementedError

    @abstractmethod
    def search_instrument(self, symbol: str, expiry_date: str, strike: float, option_type: str) -> str:
        """
        Resolves (symbol, expiry, strike, CE/PE) to a broker
        instrument_key. Raises InstrumentNotFound (exceptions.py) if
        no matching contract exists -- expired, delisted, or the
        strike isn't listed.

        expiry_date is ALWAYS an ISO date string ("YYYY-MM-DD") --
        same format get_current_expiry() below returns, and the same
        format scanner.py's days_to_expiry()/expiry_theta_ok() assume.
        A concrete broker whose own instrument master stores expiry in
        a different native format (e.g. Angel One's "DDMMMYYYY") must
        convert internally rather than requiring ISO-format callers to
        know its native format.
        """
        raise NotImplementedError

    @abstractmethod
    def get_current_expiry(self, symbol: str) -> str:
        """
        Nearest unexpired option expiry for `symbol` ("NIFTY" or
        "BANKNIFTY"), as an ISO date string ("YYYY-MM-DD") -- ALWAYS
        ISO regardless of which broker is active (see search_instrument()'s
        docstring above on why). Raises InstrumentNotFound if no
        unexpired contract is found for `symbol`.
        """
        raise NotImplementedError

    # Not an abstract method: this is an Upstox-specific convenience
    # (see upstox.py's implementation) that scanner.py's vwap_check()
    # calls via market_data.get_broker().resolve_current_month_future(symbol)
    # to auto-roll the VWAP futures contract every month with no manual
    # update. It's not part of the required interface because a minimal
    # broker implementation (e.g. one that only ever trades options)
    # might reasonably not support it -- but any broker that DOES
    # support futures should implement a method with this exact name
    # and signature so vwap_check() keeps working unchanged if you swap
    # brokers later:
    #
    #   def resolve_current_month_future(self, symbol: str) -> str: ...

    # Same treatment as resolve_current_month_future() above, added
    # 2026-07-17 for STOCK underlyings (config.STOCK_SYMBOLS):
    # option_chain.py's resolve_underlying_key() calls
    # market_data.get_broker().resolve_equity_instrument_key(symbol)
    # for any symbol that isn't one of the hardcoded indices, so a
    # stock's NSE_EQ/ISIN-based instrument_key never has to be
    # hand-typed and re-verified the way the index keys are. Not
    # abstract for the same reason resolve_current_month_future()
    # isn't -- but any broker used for stock scanning must implement:
    #
    #   def resolve_equity_instrument_key(self, symbol: str) -> str: ...
