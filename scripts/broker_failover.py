"""
broker_failover.py
-------------------
FailoverBroker: wraps a primary Broker (Angel One) and a secondary/
fallback Broker (Upstox) behind the SAME Broker interface, so
scanner.py/tracker.py/market_data.py never know failover is happening
-- they just call methods on "the broker" as always (see broker.py's
docstring on why the interface is designed this way).

DESIGN: no cross-run persisted state, on purpose. Every GitHub Actions
run constructs a fresh FailoverBroker (market_data.py does this once
per process), which always starts on "primary". If Angel One is
having an extended outage, every run pays a small cost re-detecting
that at the start of the run and falls back to Upstox for the rest of
THAT run -- but the NEXT run tries Angel One first again automatically.
This IS the "automatically restore Angel One without user
intervention" behavior the spec asks for: it falls out of the
run-per-process architecture for free, with no extra state.py schema,
no extra git commits, no extra failure modes to build and maintain.
The tradeoff: restoration happens at run granularity (every 5-15
minutes per trader.yml's cadence), not mid-run -- acceptable given
runs are short and frequent.

WITHIN one run, once FAILOVER_CONSECUTIVE_FAILURE_THRESHOLD consecutive
Angel One failures have been seen, this stops retrying Angel One for
the REST of that run and goes straight to Upstox -- so one bad run
doesn't pay Angel One's full retry cost (RETRY_MAX_ATTEMPTS backoffs
per call) on every single subsequent call too.

DUPLICATE ALERTS: this file only affects which broker DATA comes from.
It never touches state.py's messages_sent/last_processed_ts or
main.py's notify_once() -- those don't know or care which broker
served the candles that led to a trade event. A broker switch mid-run
therefore cannot by itself cause a duplicate or missing alert; that
guarantee comes from the existing separation between data-fetching
(this file) and notification (main.py), not from anything added here.

NOT wired to Telegram: broker switches are logged via logging_setup
only (visible in the GitHub Actions job log), not sent as Telegram
messages -- the spec explicitly says not to send heartbeat-style
messages, and a broker switch is an operational event, not a trade
event. Telegram wiring is easy to add later if you want visibility
there too (see log_switch() below).
"""

from datetime import datetime, timezone, timedelta

from broker import Broker, INTERVAL_MINUTES
from exceptions import AuthenticationError, BrokerAPIError, ConfigurationError, InstrumentNotFound
from config import FAILOVER_CONSECUTIVE_FAILURE_THRESHOLD, STALE_DATA_THRESHOLD_MIN, \
    MARKET_OPEN_HHMM, MARKET_CLOSE_HHMM_HEARTBEAT
from logging_setup import get_logger

IST = timezone(timedelta(hours=5, minutes=30))
log = get_logger("main")

# Every exception type a concrete Broker implementation can raise that
# should trigger failover. InstrumentNotFound is included: a contract
# missing from one broker's instrument master is worth one retry
# against the other broker's master before giving up, even though it's
# more often data-specific than a true health signal (see module
# docstring's method-by-method notes below for the one place this is
# handled differently: search_instrument()/resolve_current_month_future()
# don't count InstrumentNotFound toward the failure-streak circuit
# breaker, since a contract genuinely absent from Angel One's master
# says nothing about Angel One's HEALTH).
_FAILOVER_TRIGGERS = (AuthenticationError, BrokerAPIError, ConfigurationError, InstrumentNotFound)


def _in_market_hours(now=None) -> bool:
    now = now or datetime.now(IST)
    if now.weekday() >= 5:
        return False
    hhmm = (now.hour, now.minute)
    return MARKET_OPEN_HHMM <= hhmm <= MARKET_CLOSE_HHMM_HEARTBEAT


class FailoverBroker(Broker):
    def __init__(self, primary: Broker, primary_name: str, secondary: Broker, secondary_name: str):
        self._primary = primary
        self._primary_name = primary_name
        self._secondary = secondary
        self._secondary_name = secondary_name
        self._active_is_primary = True
        self._consecutive_primary_failures = 0
        self.switch_log = []  # [(timestamp_iso, prev, current, reason), ...] -- this run only

    # -----------------------------------------------------------------
    # Switch bookkeeping
    # -----------------------------------------------------------------

    def _active_name(self) -> str:
        return self._primary_name if self._active_is_primary else self._secondary_name

    def _switch_to_secondary(self, reason: str):
        if not self._active_is_primary:
            return  # already there
        prev = self._active_name()
        self._active_is_primary = False
        self._log_switch(prev, self._active_name(), reason)

    def _log_switch(self, prev: str, current: str, reason: str):
        entry = (datetime.now(IST).isoformat(), prev, current, reason)
        self.switch_log.append(entry)
        log.warning("BROKER SWITCH: %s -> %s | reason: %s | at %s",
                    prev, current, reason, entry[0])

    def _record_primary_failure(self, exc: Exception):
        self._consecutive_primary_failures += 1
        log.warning("%s call failed (%d/%d consecutive): %s: %s",
                    self._primary_name, self._consecutive_primary_failures,
                    FAILOVER_CONSECUTIVE_FAILURE_THRESHOLD, type(exc).__name__, exc)
        if self._consecutive_primary_failures >= FAILOVER_CONSECUTIVE_FAILURE_THRESHOLD:
            self._switch_to_secondary(
                f"{self._consecutive_primary_failures} consecutive failures "
                f"(last: {type(exc).__name__}: {exc})"
            )

    def _record_primary_success(self):
        self._consecutive_primary_failures = 0

    # -----------------------------------------------------------------
    # Generic call wrapper -- used by every Broker method below
    # -----------------------------------------------------------------

    def _call(self, method_name: str, *args, count_failures: bool = True, result_ok=None, **kwargs):
        """
        result_ok: optional callable(result) -> (bool, reason_or_None),
        checked only when the primary call returns WITHOUT raising.
        Lets get_candles()/get_option_chain() below reject an empty or
        stale-but-HTTP-200 result as a failure, through the SAME
        counting path as an exception -- deliberately not a separate
        manual call after the fact. An earlier version of this file
        called _record_primary_failure() manually after _call() had
        already recorded a success for the same result, which reset
        the streak back to 1 on every call and meant two consecutive
        stale results could never reach the switch threshold. Testing
        caught this; there is exactly one place that decides
        success/failure per call now.

        Two independent things happen on a primary failure (exception
        OR a failed result_ok check), worth being explicit about both:
        (1) THIS call still gets served from secondary regardless of
        the failure count -- the caller never sees an exception or a
        bad result just because it's the first failure of a streak;
        (2) separately, count_failures=True failures accumulate toward
        FAILOVER_CONSECUTIVE_FAILURE_THRESHOLD, which controls whether
        primary is tried FIRST again on the NEXT (different) call. A
        single transient failure therefore costs nothing but one extra
        secondary call -- primary is still tried first next time --
        while a real streak eventually stops wasting time retrying a
        broker that's actually down.
        """
        if not self._active_is_primary:
            return getattr(self._secondary, method_name)(*args, **kwargs)

        try:
            result = getattr(self._primary, method_name)(*args, **kwargs)
        except _FAILOVER_TRIGGERS as exc:
            if count_failures:
                self._record_primary_failure(exc)  # may flip _active_is_primary via threshold
            else:
                log.warning(
                    "%s.%s failed (single-call fallback only, not counted toward "
                    "the failover streak -- primary stays active for other calls): %s: %s",
                    self._primary_name, method_name, type(exc).__name__, exc
                )
            # Fall back to secondary for THIS call regardless of
            # whether the streak above flipped us over permanently.
            return getattr(self._secondary, method_name)(*args, **kwargs)

        if result_ok is not None:
            ok, reason = result_ok(result)
            if not ok:
                self._record_primary_failure(BrokerAPIError(reason))
                return getattr(self._secondary, method_name)(*args, **kwargs)

        if count_failures:
            self._record_primary_success()
        return result

    # -----------------------------------------------------------------
    # Broker interface
    # -----------------------------------------------------------------

    def get_candles(self, instrument_key: str, interval: str, since_iso: str = None) -> list:
        def validate(candles):
            # "Missing historical candles" / "stale market data"
            # triggers -- only meaningful for a bulk fetch
            # (since_iso=None) during market hours; an incremental
            # since_iso fetch legitimately returns [] between polls
            # when nothing new has printed yet, so that case is always
            # considered fine.
            if since_iso is not None or not _in_market_hours():
                return True, None
            if not candles:
                return False, "empty candle list on a bulk fetch"
            latest_ts = datetime.fromisoformat(candles[-1]["timestamp"])
            age_min = (datetime.now(IST) - latest_ts).total_seconds() / 60
            # BUGFIX: threshold must account for the candle interval
            # itself. broker.drop_incomplete_trailing_candle() (applied
            # by every concrete broker's get_candles()) deliberately
            # withholds the still-forming trailing candle, so the
            # newest AVAILABLE complete candle is, by design, up to
            # one full interval old -- for 15-min candles that's up to
            # 15 minutes, which used to exceed a flat 10-min threshold
            # and would falsely trigger failover on a perfectly healthy
            # system right before each 15-min candle closes. A flat
            # threshold only ever made sense for the smallest interval;
            # this scales it to the interval actually being checked.
            interval_minutes = INTERVAL_MINUTES.get(interval, 1)
            effective_threshold = STALE_DATA_THRESHOLD_MIN + interval_minutes
            if age_min > effective_threshold:
                return False, f"latest candle is {age_min:.0f} min stale (threshold {effective_threshold} min for {interval})"
            return True, None

        return self._call("get_candles", instrument_key, interval, since_iso=since_iso,
                           result_ok=validate)

    def get_ltp(self, instrument_key: str) -> float:
        return self._call("get_ltp", instrument_key)

    def get_daily_candles(self, instrument_key: str, count: int) -> list:
        # Phase 1, premarket-only. Same reasoning as
        # download_instrument_master() above: a bulk historical-data
        # call succeeding or failing reflects that broker's daily-bar
        # endpoint on that one call, not primary-broker health, so it
        # doesn't count toward the failover streak -- but a failure
        # still falls through to the secondary broker before
        # opportunity_score.py drops the symbol from scoring entirely.
        def validate(candles):
            return (True, None) if candles else (False, "empty daily candle list")
        return self._call("get_daily_candles", instrument_key, count,
                           count_failures=False, result_ok=validate)

    def get_batch_quotes(self, instrument_keys: list) -> dict:
        # Phase 1, premarket-only. Same reasoning as get_daily_candles()
        # above.
        def validate(quotes):
            return (True, None) if quotes else (False, "empty batch quote response")
        return self._call("get_batch_quotes", instrument_keys,
                           count_failures=False, result_ok=validate)

    def get_option_chain(self, underlying_key: str, expiry_date: str) -> list:
        def validate(chain):
            return (True, None) if chain else (False, "empty option chain")

        return self._call("get_option_chain", underlying_key, expiry_date, result_ok=validate)

    def search_instrument(self, symbol: str, expiry_date: str, strike: float, option_type: str) -> str:
        # InstrumentNotFound here is data-specific, not a health
        # signal -- don't let it trip the failover streak on its own
        # (count_failures=False), but DO still try the other broker's
        # master for this one lookup before giving up entirely.
        return self._call("search_instrument", symbol, expiry_date, strike, option_type,
                           count_failures=False)

    def resolve_current_month_future(self, symbol: str) -> str:
        return self._call("resolve_current_month_future", symbol, count_failures=False)

    def resolve_and_fetch_current_month_future_candles(self, symbol: str, interval: str) -> list:
        """
        BUGFIX (2026-07-22, live failure): resolve_current_month_future()
        and get_candles() used to be called as two INDEPENDENT _call()s
        from option_chain.py's _futures_vwap(). Each one independently
        decides primary vs. secondary based on the CURRENT
        _active_is_primary flag at the moment IT runs -- so a
        resolve-time single-call fallback to secondary (which never
        flips _active_is_primary, by design -- see resolve_current_month_
        future()'s count_failures=False above) could hand back an
        instrument_key in the SECONDARY broker's format, which the very
        next get_candles() call -- still routed to primary, since the
        flag never flipped -- then sent to the PRIMARY broker's API.
        Instrument keys are broker-specific (Upstox: "NSE_FO|<key>",
        Angel One: "NFO|<token>"), so mixing them produces a
        guaranteed-invalid request. This is exactly what happened live:
        GitHub Actions run 2026-07-22T09:20:41Z shows
        "Upstox.resolve_current_month_future failed (single-call
        fallback...)" immediately followed by a 400 from Upstox's API
        for instrument key "NFO|61303" -- an Angel One-formatted key
        (Angel One was the secondary that resolved it) sent to Upstox
        (which was, and remained, primary) on the very next call.

        This method makes resolution + fetch ONE atomic broker choice --
        whichever broker resolves the key is guaranteed to be the same
        broker that fetches candles for it. Returns the RAW (unsliced)
        candle list, same shape get_candles() returns -- callers that
        want only the most recent N candles slice it themselves (see
        market_data.get_recent_futures_candles()).
        """
        if self._active_is_primary:
            try:
                key = self._primary.resolve_current_month_future(symbol)
                candles = self._primary.get_candles(key, interval)
            except _FAILOVER_TRIGGERS as exc:
                log.warning(
                    "%s.resolve_and_fetch_current_month_future_candles failed "
                    "(single-call fallback only, not counted toward the "
                    "failover streak -- primary stays active for other "
                    "calls): %s: %s",
                    self._primary_name, type(exc).__name__, exc
                )
                key = self._secondary.resolve_current_month_future(symbol)
                candles = self._secondary.get_candles(key, interval)
            else:
                self._record_primary_success()
        else:
            key = self._secondary.resolve_current_month_future(symbol)
            candles = self._secondary.get_candles(key, interval)
        return candles

    def resolve_equity_instrument_key(self, symbol: str) -> str:
        # Same reasoning as resolve_current_month_future() above: a
        # missing/mismatched equity row is data-specific, not a
        # primary-health signal, so it doesn't count toward the
        # failover streak -- but a fresh InstrumentNotFound still
        # falls through to the secondary broker for this one call.
        return self._call("resolve_equity_instrument_key", symbol, count_failures=False)

    def get_current_expiry(self, symbol: str) -> str:
        # Same reasoning as search_instrument()/resolve_current_month_future()
        # above: an expiry-list lookup is data-specific, not a
        # primary-health signal, so it doesn't count toward the
        # failover streak -- but a fresh InstrumentNotFound still
        # falls through to the secondary broker for this one call.
        return self._call("get_current_expiry", symbol, count_failures=False)

    def download_instrument_master(self) -> list:
        # Phase 2 (instrument_master.py). Same reasoning as
        # get_current_expiry() above: a large bulk data download
        # succeeding or failing reflects that ONE broker's instrument
        # master endpoint on that particular day, not primary-broker
        # health in the sense the failover streak exists to detect --
        # so this doesn't count toward it (count_failures=False), but
        # a failure here still falls through to try the secondary
        # broker's master before instrument_master.py gives up for the
        # day and its caller (config.load_symbols_for_today()) falls
        # back to the fixed watchlist.
        def validate(rows):
            return (True, None) if rows else (False, "empty instrument master")

        return self._call("download_instrument_master", count_failures=False, result_ok=validate)

    # -----------------------------------------------------------------
    # Introspection -- for heartbeat_check.py / main.py, not part of
    # the required Broker interface.
    # -----------------------------------------------------------------

    def active_broker_name(self) -> str:
        return self._active_name()

    def raw_primary(self) -> Broker:
        """The underlying primary broker instance, unwrapped -- for
        checks that must specifically target one broker regardless of
        which is currently "primary" vs. "secondary" in this wrapper
        (e.g. auth.validate_upstox_token() needs the real
        UpstoxBroker, not this wrapper -- but note market_data.py's
        get_upstox_broker()/get_angelone_broker() already give you
        that directly and don't care which one is primary here)."""
        return self._primary

    def raw_secondary(self) -> Broker:
        return self._secondary