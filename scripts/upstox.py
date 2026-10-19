"""
upstox.py
---------
Concrete Broker implementation for Upstox. This is the only file that
should know Upstox's specific endpoint paths, request/response shapes,
or instrument-key format -- everything else talks to the Broker
interface in broker.py.

VERIFIED against Upstox's live docs (July 2026) -- two real issues
found and fixed from the original best-effort version:

1. get_candles() was calling the v2 `/v2/historical-candle/intraday/
   {instrument_key}/{interval}` endpoint with "15minute" as a path
   segment. That endpoint is now marked **Deprecated** in Upstox's own
   docs and its interval path segment ONLY ever supported "1minute"
   and "30minute" -- "15minute" was never a valid value there, v2 or
   not. Since multi_timeframe_confirms(), market_regime(), and
   vwap_check() in scanner.py all request "15minute" underlying
   candles, every one of those calls would have failed at runtime.
   Fixed: now calls the v3 endpoint (`/v3/historical-candle/intraday/
   {instrument_key}/{unit}/{interval}`), which explicitly supports
   1/3/5/15/30-minute (and more) via `minutes/<N>` path segments and
   is the endpoint Upstox is actively pointing new integrations at.
   Response shape is backward compatible (same candle array format),
   plus an extra open-interest field (index 6) this project doesn't
   currently consume.

2. get_option_chain() returned Upstox's raw JSON response unmodified --
   a list of per-strike dicts with NESTED `call_options`/`put_options`
   sub-dicts (each itself containing a `market_data` dict and an
   `option_greeks` dict). But broker.py's Broker interface (and
   angelone.py's actual implementation) both expect a FLAT per-strike
   dict (`call_oi`, `put_bid`, `call_delta`, etc.) -- the two brokers
   would have handed score_strike() two different shapes depending on
   which one failover happened to be using. Fixed: get_option_chain()
   now flattens Upstox's nested response into the same flat shape
   angelone.py already produces, confirmed field-for-field against the
   schema in Upstox's Put/Call Option Chain docs (see get_option_chain()'s
   docstring below for the exact nested shape this flattening is based
   on, and which fields are Upstox-only extras).

search_instrument() and resolve_current_month_future()'s field-name
assumptions (name, expiry, strike_price, instrument_type,
instrument_key) against the instrument master are unchanged from the
original best-effort version -- these weren't re-verified against a
live downloaded master file this pass; the schema still matches
Upstox's documented instrument master shape as of this writing, but
confirm against an actual downloaded file before depending on it live,
same as any broker integration.

UPDATE (2026-10-19, production backfill audit, Oct 5-9 logs): the
"not independently confirmed" caveat on resolve_current_month_future()
below was finally checked against a live downloaded master
(debug_instrument_masters.py's futures diagnostic section) and the
`trading_symbol.endswith("FUT")` assumption was WRONG -- see that
function's updated docstring and BUGFIX note for the real format and
the fix. search_instrument() (used for options, CE/PE) still carries
the original, never-independently-reverified caveat -- it wasn't part
of this audit's evidence and is NOT touched by this change.
"""

import gzip
import json
from datetime import datetime, timezone, timedelta

from broker import Broker, drop_incomplete_trailing_candle
from auth import get_upstox_token
from retry_utils import request_with_retry
from exceptions import BrokerAPIError, InstrumentNotFound
from cache import TTLCache
from logging_setup import get_logger
from config import SYMBOLS

IST = timezone(timedelta(hours=5, minutes=30))
BASE_URL = "https://api.upstox.com/v2"
BASE_URL_V3 = "https://api.upstox.com/v3"
INSTRUMENT_MASTER_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
# Added 2026-07-23 for SENSEX -- the NSE-only master above has no BFO/
# BSE_FO rows at all, confirmed via Upstox's own per-exchange file
# naming convention (NSE.json.gz / BSE.json.gz / MCX.json.gz /
# complete.json.gz, documented on their Instruments page). Only
# fetched when SENSEX is actually in config.SYMBOLS -- see
# _fetch_instrument_master()'s docstring for why, and config.py's
# SYMBOLS comment for the "remove SENSEX to revert" promise this keeps.
INSTRUMENT_MASTER_URL_BSE = "https://assets.upstox.com/market-quote/instruments/exchange/BSE.json.gz"
log = get_logger("api")

# v3 intraday candle endpoint: /v3/historical-candle/intraday/{instrument_key}/{unit}/{interval}
# -- "minutes"/<N> replaces v2's fixed "1minute"/"30minute" interval
# names and is the only way to get 15-minute (or 3/5-minute) intraday
# candles at all; v2's intraday endpoint is deprecated and never
# supported anything but 1 and 30 minute.
_INTERVAL_UNIT_VALUE = {
    "1minute": ("minutes", "1"),
    "5minute": ("minutes", "5"),
    "15minute": ("minutes", "15"),
}

# Duplicated from scanner.py's UNDERLYING_INSTRUMENT_KEYS on purpose --
# get_current_expiry() below needs to map symbol -> underlying
# instrument_key, but importing scanner.py here would create a
# circular import (scanner.py -> market_data.py -> upstox.py ->
# scanner.py). These are Upstox's own documented NSE index keys and
# don't change, so a second copy here can't drift from scanner.py's in
# practice -- if you ever add a symbol, update both.
UNDERLYING_INSTRUMENT_KEYS = {
    "NIFTY": "NSE_INDEX|Nifty 50",
    "BANKNIFTY": "NSE_INDEX|Nifty Bank",
    # Kept in sync with option_chain.py's copy -- see that file's
    # comment for the verification caveat on this one.
    "MIDCPNIFTY": "NSE_INDEX|NIFTY MID SELECT",
    # Added 2026-07-23, kept in sync with option_chain.py's copy --
    # see that file's comment for the verification caveat.
    "SENSEX": "BSE_INDEX|SENSEX",
}

# Names Upstox's instrument master uses for index derivatives' "name"
# field -- used ONLY to classify a row as an index vs. stock contract
# for download_instrument_master()'s contract_class (Upstox's own
# instrument_type field distinguishes CE/PE/FUT/EQ, not index-vs-stock,
# so this second check is needed the same way angelone.py's
# _option_instrument_type() needs config.STOCK_SYMBOLS). Includes
# SENSEX/FINNIFTY even though they're not in UNDERLYING_INSTRUMENT_KEYS
# yet (still deferred per config.py) -- classification should be
# correct the day either is wired in, not need a second edit here too.
_INDEX_NAMES = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX"}


def _contract_class(raw_instrument_type: str, name: str) -> str:
    """
    Upstox's instrument_type field is CONTRACT type (CE/PE/FUT/EQ), not
    index-vs-stock -- this derives the broker-agnostic contract_class
    (see broker.py's Broker.download_instrument_master() docstring) by
    combining it with whether `name` is a known index.
    """
    is_index = name in _INDEX_NAMES
    if raw_instrument_type in ("CE", "PE"):
        return "INDEX_OPTION" if is_index else "STOCK_OPTION"
    if raw_instrument_type == "FUT":
        return "INDEX_FUTURE" if is_index else "STOCK_FUTURE"
    if raw_instrument_type == "EQ":
        return "EQUITY"
    return raw_instrument_type or "UNKNOWN"


def _normalize_expiry(expiry_raw) -> str:
    """
    Upstox's instrument-master JSON gives F&O "expiry" as epoch
    MILLISECONDS (int/float) on some row types and an already-ISO
    string on others -- the same inconsistency
    resolve_current_month_future() above already has to handle for
    futures rows (see that method's docstring). Reused here so
    download_instrument_master() always returns "YYYY-MM-DD" or None,
    regardless of which representation this particular row used.
    """
    if not expiry_raw:
        return None
    if isinstance(expiry_raw, str):
        try:
            return datetime.fromisoformat(expiry_raw).date().isoformat()
        except ValueError:
            return None
    try:
        return datetime.fromtimestamp(expiry_raw / 1000, tz=IST).date().isoformat()
    except (TypeError, ValueError, OSError):
        return None


class UpstoxBroker(Broker):
    def __init__(self):
        # Lives exactly as long as one GitHub Actions run (this object
        # is created once per run by market_data.py) -- see cache.py's
        # docstring for why that's the right cache lifetime here.
        self._instrument_cache = TTLCache(ttl_seconds=3600)
        self._chain_cache = TTLCache(ttl_seconds=120)

    def _headers(self):
        return {
            "Authorization": f"Bearer {get_upstox_token()}",
            "Accept": "application/json",
        }

    def get_candles(self, instrument_key: str, interval: str, since_iso: str = None) -> list:
        if interval not in _INTERVAL_UNIT_VALUE:
            raise BrokerAPIError(f"unsupported interval: {interval}")
        unit, value = _INTERVAL_UNIT_VALUE[interval]

        since_dt = None
        if since_iso:
            since_dt = datetime.fromisoformat(since_iso)
            if since_dt.tzinfo is None:
                since_dt = since_dt.replace(tzinfo=IST)

        # v3 intraday endpoint -- see module docstring point 1 for why
        # this replaced the old v2 ".../intraday/{instrument_key}/{interval}"
        # path (deprecated, and never supported 15-minute in the first place).
        url = f"{BASE_URL_V3}/historical-candle/intraday/{instrument_key}/{unit}/{value}"
        resp = request_with_retry("GET", url, headers=self._headers(), timeout=15,
                                   error_cls=BrokerAPIError)
        payload = resp.json()

        raw_candles = payload.get("data", {}).get("candles", [])
        out = []
        for c in raw_candles:
            ts = datetime.fromisoformat(c[0])
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=IST)
            if since_dt is not None and ts <= since_dt:
                continue
            out.append({
                "timestamp": ts.isoformat(),
                "open": float(c[1]),
                "high": float(c[2]),
                "low": float(c[3]),
                "close": float(c[4]),
                "volume": float(c[5]) if len(c) > 5 and c[5] is not None else 0.0,
                # c[6] (open interest) is a v3 addition, not part of
                # broker.py's documented candle shape -- not surfaced
                # here since nothing downstream reads it yet.
            })
        out.sort(key=lambda c: c["timestamp"])
        return drop_incomplete_trailing_candle(out, interval)

    def get_ltp(self, instrument_key: str) -> float:
        url = f"{BASE_URL}/market-quote/ltp"
        resp = request_with_retry("GET", url, headers=self._headers(), timeout=10,
                                   params={"instrument_key": instrument_key},
                                   error_cls=BrokerAPIError)
        payload = resp.json()
        data = payload.get("data", {})
        for _, quote in data.items():  # single-instrument request -> exactly one entry
            return float(quote["last_price"])
        raise InstrumentNotFound(f"no LTP returned for {instrument_key}")

    def get_native_pcr(self, instrument_key: str, expiry_date: str, date_str: str,
                        bucket_interval: int = 60):
        """
        Upstox's own dedicated PCR endpoint -- added 2026-08-14
        (architecture cleanup audit) as a DIAGNOSTIC-ONLY parity check
        against chain_metrics.overall_pcr() (this repo's existing,
        self-computed PCR from the fetched option chain). NOT part of
        the abstract Broker interface (AngelOne has no equivalent
        exposed here) and NOT called from options_intelligence/engine.py
        itself (that package deliberately stays independent of live
        broker clients at import time -- see its own analyze()
        docstring) -- callers reach this directly via market_data._upstox,
        same pattern get_india_vix() uses.

        Endpoint confirmed against Upstox's own documented example:
        GET /v2/market/pcr?instrument_key=...&expiry=...&date=...&bucket_interval=...
        -- returns PCR at intraday intervals from market open. This
        method intentionally does NOT replace the self-computed PCR
        anywhere; it exists so the two can be logged side by side and
        compared before any future decision to switch (see this
        session's own audit for why "verify parity, don't replace yet"
        was the explicit instruction).

        Returns the LATEST bucket's PCR value from the response, or
        None if the response shape doesn't contain one -- never raises
        (this is optional diagnostic context, must never interrupt a
        scan cycle).
        """
        try:
            url = f"{BASE_URL}/market/pcr"
            resp = request_with_retry(
                "GET", url, headers=self._headers(), timeout=10,
                params={
                    "instrument_key": instrument_key, "expiry": expiry_date,
                    "date": date_str, "bucket_interval": bucket_interval,
                },
                error_cls=BrokerAPIError,
            )
            payload = resp.json()
            data = payload.get("data")
            if isinstance(data, list) and data:
                return data[-1].get("pcr")
            if isinstance(data, dict):
                return data.get("pcr")
            return None
        except Exception:
            return None

    def get_option_chain(self, underlying_key: str, expiry_date: str) -> list:
        """
        Upstox's raw response for one strike looks like this (verified
        against Upstox's live Put/Call Option Chain docs, July 2026):

            {
              "expiry": "...", "strike_price": 21100,
              "underlying_spot_price": 22976.2,
              "call_options": {
                "instrument_key": "NSE_FO|...",
                "market_data": {"ltp": .., "volume": .., "oi": ..,
                                 "bid_price": .., "ask_price": ..,
                                 "prev_oi": ..},
                "option_greeks": {"delta": .., "iv": .., "theta": ..,
                                   "gamma": .., "vega": .., "pop": ..}
              },
              "put_options": { ...same shape... }
            }

        i.e. NESTED per call/put, not the flat per-strike dict
        broker.py's interface documents. Flattened below into the same
        shape angelone.py's get_option_chain() already returns
        (call_instrument_key/call_oi/call_bid/... , put_...), so
        score_strike() can read either broker's chain identically --
        this matters especially across a mid-day Angel One -> Upstox
        failover. bid/ask above map from Upstox's *_price fields;
        call_prev_oi/put_prev_oi and call_iv/put_iv are Upstox-only
        extras (angelone.py doesn't provide them) -- optional, useful
        for OI-build/IV checks if you want them, but don't rely on
        them being present if you ever add a third broker.
        """
        cache_key = (underlying_key, expiry_date)
        cached = self._chain_cache.get(cache_key)
        if cached is not None:
            return cached

        url = f"{BASE_URL}/option/chain"
        resp = request_with_retry("GET", url, headers=self._headers(), timeout=15,
                                   params={"instrument_key": underlying_key, "expiry_date": expiry_date},
                                   error_cls=BrokerAPIError)
        raw_chain = resp.json().get("data", [])

        chain = []
        _iv_sample_logged = False
        for row in raw_chain:
            entry = {
                "strike_price": row.get("strike_price"),
                "underlying_spot_price": row.get("underlying_spot_price"),
            }
            for side_key, prefix in (("call_options", "call_"), ("put_options", "put_")):
                side = row.get(side_key) or {}
                if not side:
                    continue
                entry[prefix + "instrument_key"] = side.get("instrument_key")
                market = side.get("market_data") or {}
                entry[prefix + "ltp"] = market.get("ltp", 0.0)
                entry[prefix + "oi"] = market.get("oi", 0.0)
                entry[prefix + "prev_oi"] = market.get("prev_oi")
                entry[prefix + "volume"] = market.get("volume", 0.0)
                # BUGFIX (2026-08-15, P0 bid/ask evidence audit): used
                # to default to 0.0 when Upstox's response genuinely
                # lacked bid_price/ask_price -- silently making a
                # MISSING quote indistinguishable from a real zero-width
                # spread. Missing now correctly stays None all the way
                # through to filters.liquidity_ok() (see that function's
                # own updated docstring) instead of masquerading as
                # perfect liquidity. ltp/oi/volume above are UNCHANGED
                # (out of this fix's approved scope -- only bid/ask were
                # audited and approved for this specific correction).
                entry[prefix + "bid"] = market.get("bid_price")
                entry[prefix + "ask"] = market.get("ask_price")
                greeks = side.get("option_greeks") or {}
                raw_iv = greeks.get("iv")
                entry[prefix + "delta"] = greeks.get("delta")
                entry[prefix + "iv"] = raw_iv
                if not _iv_sample_logged and raw_iv is not None:
                    # IV SCALE VERIFICATION (diagnostic only, no normalization/threshold
                    # change here) -- logs the untouched raw option_greeks.iv value
                    # exactly as Upstox's /v2/option/chain endpoint returned it, so
                    # its scale can be confirmed from real traffic. NOTE: Upstox's
                    # OTHER endpoints (v3 option-greek, market-data-feed websocket)
                    # return iv as a 0-1 fraction -- this is specifically the
                    # /option/chain endpoint this method calls. See
                    # IV_SCALE_VERIFICATION.md for the finding.
                    #
                    # RENAMED (2026-08-10): this is the FIRST row in the raw chain
                    # with a non-None IV -- often a deep OTM/edge strike nowhere
                    # near any strike actually being traded/scored this cycle (that
                    # traded leg's OWN entry[prefix+"iv"] a few lines above is set
                    # correctly and independently on its own loop iteration; this
                    # log line never feeds it). "IV_SAMPLE" alone read as if it
                    # described the strike currently being evaluated -- it does
                    # not. See scanner.py's CONTRACT_RESOLUTION log line for the
                    # one that actually describes the traded leg.
                    log.info(
                        "IV_SAMPLE_CHAINWIDE_DIAGNOSTIC_ONLY upstox instrument_key=%s strike=%s side=%s raw_iv=%r "
                        "(first available quote in this chain fetch, NOT the traded leg)",
                        entry.get(prefix + "instrument_key"), row.get("strike_price"),
                        prefix.strip("_"), raw_iv,
                    )
                    _iv_sample_logged = True
            chain.append(entry)

        self._chain_cache.set(cache_key, chain)
        return chain

    def _instrument_master(self) -> list:
        return self._instrument_cache.get_or_set("master", self._fetch_instrument_master)

    def _fetch_instrument_master(self) -> list:
        # Upstox publishes a daily instrument master as a gzip-compressed
        # JSON download (not behind the authenticated REST API) at the
        # URL above, as of this writing -- confirm it's still current;
        # this kind of static-asset URL has moved before on other brokers.
        resp = request_with_retry("GET", INSTRUMENT_MASTER_URL, timeout=30,
                                   error_cls=BrokerAPIError)
        master = json.loads(gzip.decompress(resp.content))

        if "SENSEX" in SYMBOLS:
            # NSE.json.gz has no BFO/BSE_FO rows at all -- SENSEX
            # search_instrument()/get_current_expiry()/
            # resolve_current_month_future() below all iterate this
            # SAME merged list with no exchange filtering of their own
            # (they already match on name/expiry/strike_price/
            # instrument_type, which is exchange-agnostic), so merging
            # BSE's rows in here is the only change those methods need
            # -- see this file's UNDERLYING_INSTRUMENT_KEYS entry for
            # SENSEX and _INDEX_NAMES above for the rest of the wiring.
            #
            # Degrades gracefully (logs, does NOT raise) if this
            # second fetch fails -- a BSE-side hiccup must never take
            # down NIFTY/BANKNIFTY's own instrument master, which this
            # same method already serves. A SENSEX lookup on a master
            # missing its BSE half just raises the same
            # InstrumentNotFound every other "symbol not in master"
            # case already does -- not a new failure mode.
            try:
                bse_resp = request_with_retry("GET", INSTRUMENT_MASTER_URL_BSE, timeout=30,
                                               error_cls=BrokerAPIError)
                master = master + json.loads(gzip.decompress(bse_resp.content))
            except BrokerAPIError as exc:
                log.error(
                    "Failed to fetch BSE instrument master (needed for SENSEX) -- "
                    "SENSEX lookups will raise InstrumentNotFound this run, NIFTY/"
                    "BANKNIFTY/MIDCPNIFTY are unaffected. Error: %s", exc,
                )

        return master

    def download_instrument_master(self) -> list:
        """
        Phase 2 (instrument_master.py). Normalizes the SAME cached raw
        master _instrument_master() already provides (no extra
        download -- reuses the in-process TTLCache set up in __init__)
        into the broker-agnostic row shape documented on
        Broker.download_instrument_master() in broker.py.

        Field-name mapping (per this file's other methods' documented
        schema assumptions -- see their docstrings for the "confirm
        against a real downloaded file" caveats, which apply here too):
          name             -> underlying_symbol (index) / not used for equities
          trading_symbol   -> symbol / trading_symbol
          instrument_type  -> "CE"/"PE" for options, "FUT" for futures,
                               "EQ" for equities (Upstox uses this field
                               for CONTRACT type, not index-vs-stock --
                               see _contract_class() below for how the
                               INDEX_ vs STOCK_ split is derived)
          strike_price     -> strike_price (already plain rupee terms on
                               Upstox -- no x100-style scaling to undo,
                               unlike Angel One's scrip master)
          expiry           -> expiry, ISO string OR epoch-ms int depending
                               on row type (same inconsistency
                               resolve_current_month_future() already
                               handles -- reused here via _normalize_expiry())
          lot_size         -> lot_size
          instrument_key   -> instrument_key
          exchange         -> exchange
        """
        raw_master = self._instrument_master()
        normalized = []
        for row in raw_master:
            instrument_key = row.get("instrument_key")
            if not instrument_key:
                continue  # unusable row -- no way to trade it even if everything else parsed
            raw_type = row.get("instrument_type")
            name = row.get("name")
            trading_symbol = row.get("trading_symbol")
            option_type = raw_type if raw_type in ("CE", "PE") else None
            contract_class = _contract_class(raw_type, name)
            expiry_iso = _normalize_expiry(row.get("expiry"))
            strike_price = row.get("strike_price")
            try:
                strike_price = float(strike_price) if strike_price not in (None, "", 0) else None
            except (TypeError, ValueError):
                strike_price = None
            normalized.append({
                "symbol": trading_symbol or name,
                # BUGFIX (2026-09-19, live diagnostic confirmed): used
                # to be `name` unconditionally -- for INDEX rows
                # (NIFTY/BANKNIFTY) Upstox's raw "name" field already
                # IS the short ticker ("BANKNIFTY"), so this happened
                # to work there. For STOCK option rows, raw "name" is
                # the FULL company name ("TATA STEEL LIMITED",
                # "HINDALCO  INDUSTRIES  LTD") while Upstox's response
                # ALREADY provides a separate, correct raw
                # "underlying_symbol" field holding the short ticker
                # ("TATASTEEL", "HINDALCO") -- confirmed via a live
                # debug_futures_masters.py run against the real
                # downloaded master (2026-09-18). This code was
                # discarding that correct raw field and overwriting it
                # with the full company name instead, which is why
                # instrument_master.validate_symbol()'s
                # `r.get("underlying_symbol") == symbol` (comparing
                # against the bare ticker) could NEVER match these
                # rows -- explaining the long-standing, always-the-
                # same-14-symbols "not F&O eligible" false negative.
                # Now prefers the raw response's own "underlying_symbol"
                # field (confirmed correct for both indices AND stocks
                # in the same diagnostic run), falling back to `name`
                # only if that raw field is ever genuinely absent.
                "underlying_symbol": row.get("underlying_symbol") or name,
                "exchange": row.get("exchange") or row.get("segment"),
                "contract_class": contract_class,
                "option_type": option_type,
                "expiry": expiry_iso,
                "strike_price": strike_price,
                "lot_size": row.get("lot_size"),
                "trading_symbol": trading_symbol,
                "instrument_key": instrument_key,
            })
        return normalized

    def get_daily_candles(self, instrument_key: str, count: int) -> list:
        """
        Phase 1 (opportunity_score.py). Upstox's v3 historical-candle
        endpoint takes an explicit unit ("days") and a from/to date
        range rather than a candle count, so `count` here is
        approximate: requests roughly count*1.6 CALENDAR days back
        (accounting for weekends/holidays -- NOT using holiday_guard's
        NSE holiday list, since over-requesting a few extra days and
        trimming client-side is simpler and cheaper than importing
        that dependency here) and trims to the last `count` rows.

        UNVERIFIED URL PATTERN: modeled on the same
        "/v3/historical-candle/{instrument_key}/{unit}/{value}/{to}/{from}"
        shape as the intraday endpoint above -- confirm the exact path
        and date-format against a live Upstox v3 API response before
        relying on this (same caveat this file already carries for
        MIDCPNIFTY's instrument key and the STOCK_SYMBOLS instrument-key
        resolution path -- see their docstrings).
        """
        to_dt = datetime.now(IST).date() - timedelta(days=1)  # yesterday -- today's daily candle doesn't exist pre-market
        from_dt = to_dt - timedelta(days=int(count * 1.6) + 5)

        url = (f"{BASE_URL_V3}/historical-candle/{instrument_key}/days/1/"
               f"{to_dt.isoformat()}/{from_dt.isoformat()}")
        resp = request_with_retry("GET", url, headers=self._headers(), timeout=15,
                                   error_cls=BrokerAPIError)
        payload = resp.json()
        raw_candles = payload.get("data", {}).get("candles", [])
        out = []
        for c in raw_candles:
            ts = datetime.fromisoformat(c[0])
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=IST)
            out.append({
                "timestamp": ts.isoformat(), "open": float(c[1]), "high": float(c[2]),
                "low": float(c[3]), "close": float(c[4]),
                "volume": float(c[5]) if len(c) > 5 and c[5] is not None else 0.0,
            })
        out.sort(key=lambda c: c["timestamp"])
        return out[-count:] if len(out) > count else out

    def get_previous_session_candles(self, instrument_key: str, interval: str, count: int) -> list:
        """
        FIX 2 (ATR warmup, 2026-07-30, config.WARMUP_CANDLE_CONTINUITY_ENABLED).
        Not part of the abstract Broker interface -- same duck-typed,
        opt-in-only convention as get_daily_candles()/get_batch_quotes()
        above (see broker.py's comments on those).

        get_candles() above only ever hits the ".../intraday/..."
        endpoint, which has NO from/to params and always returns just
        today. This instead uses the same v3 date-range
        "historical-candle" endpoint get_daily_candles() already uses
        for daily bars, but with a minutes unit/value -- same
        UNVERIFIED URL PATTERN caveat get_daily_candles() carries
        (confirm against a live response before relying on this).
        Over-fetches a week of calendar days ending yesterday and
        trims to the last `count` candles client-side, same
        "simpler than an NSE holiday calendar" approach as
        get_daily_candles().

        Returns [] (never raises) on any failure -- this is a warmup
        nicety, not a hard dependency; callers must fall back to
        today-only candles rather than let this take down the scan.
        """
        if interval not in _INTERVAL_UNIT_VALUE:
            raise BrokerAPIError(f"unsupported interval: {interval}")
        unit, value = _INTERVAL_UNIT_VALUE[interval]

        to_dt = datetime.now(IST).date() - timedelta(days=1)
        from_dt = to_dt - timedelta(days=6)  # over-fetch a week of calendar days to clear weekends/holidays

        url = (f"{BASE_URL_V3}/historical-candle/{instrument_key}/{unit}/{value}/"
               f"{to_dt.isoformat()}/{from_dt.isoformat()}")
        try:
            resp = request_with_retry("GET", url, headers=self._headers(), timeout=15,
                                       error_cls=BrokerAPIError)
            payload = resp.json()
            raw_candles = payload.get("data", {}).get("candles", [])
        except Exception:
            return []

        out = []
        for c in raw_candles:
            ts = datetime.fromisoformat(c[0])
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=IST)
            out.append({
                "timestamp": ts.isoformat(),
                "open": float(c[1]),
                "high": float(c[2]),
                "low": float(c[3]),
                "close": float(c[4]),
                "volume": float(c[5]) if len(c) > 5 and c[5] is not None else 0.0,
            })
        out.sort(key=lambda c: c["timestamp"])
        return out[-count:] if len(out) > count else out

    def get_batch_quotes(self, instrument_keys: list) -> dict:
        """
        Phase 1. Upstox's /v2 quotes endpoint accepts a comma-separated
        `instrument_key` query param covering many symbols in one call
        -- this is the actual API-call-count reduction Phase 1's
        "don't fully call out to all 100 stocks individually"
        requirement depends on. UNVERIFIED response-key format
        (Upstox's quote response keys each entry by instrument_key with
        `:` replaced by `%7C`-style escaping in some SDK versions --
        confirm against a live response before trusting this
        unescape-check, same caveat as get_daily_candles() above).
        Chunked at 500 keys/request (documented Upstox limit as of this
        file's other endpoints) even though Phase 1's ~100-symbol
        universe will never actually hit that in one call.
        """
        out = {}
        chunk_size = 500
        for i in range(0, len(instrument_keys), chunk_size):
            chunk = instrument_keys[i:i + chunk_size]
            url = f"{BASE_URL}/market-quote/quotes"
            resp = request_with_retry("GET", url, headers=self._headers(), timeout=15,
                                       params={"instrument_key": ",".join(chunk)},
                                       error_cls=BrokerAPIError)
            payload = resp.json().get("data", {})
            for raw_key, row in payload.items():
                key = row.get("instrument_token") or raw_key.replace("%7C", "|")
                ohlc = row.get("ohlc", {})
                out[key] = {
                    "ltp": float(row.get("last_price", 0) or 0),
                    "prev_close": float(ohlc.get("close", 0) or 0),
                    "volume": float(row.get("volume", 0) or 0),
                }
        return out

    def search_instrument(self, symbol: str, expiry_date: str, strike: float, option_type: str) -> str:
        master = self._instrument_master()
        # Field names below (name, expiry, strike_price, instrument_type,
        # instrument_key) match Upstox's documented instrument master
        # schema as of this writing -- confirm against an actual
        # downloaded file before depending on this, field names/casing
        # have changed on Upstox before.
        for row in master:
            if (row.get("name") == symbol
                    and row.get("expiry") == expiry_date
                    and float(row.get("strike_price", -1)) == strike
                    and row.get("instrument_type") == option_type):
                return row["instrument_key"]
        raise InstrumentNotFound(
            f"no contract found: {symbol} {expiry_date} {strike}{option_type} "
            f"(expired, delisted, or strike not listed)"
        )

    def get_current_expiry(self, symbol: str) -> str:
        """
        Nearest unexpired option expiry for `symbol`, as ISO
        ("YYYY-MM-DD") -- see broker.py's Broker.get_current_expiry()
        docstring for why this must always be ISO regardless of broker.

        Verified against Upstox's public Option Contracts docs:
        GET /v2/option/contract?instrument_key=... returns every
        listed contract for the underlying, each with an "expiry"
        field already in "YYYY-MM-DD" form -- so no format conversion
        is needed here, unlike angelone.py's equivalent.
        """
        underlying_key = UNDERLYING_INSTRUMENT_KEYS.get(symbol)
        if underlying_key is None:
            # Not one of the hardcoded indices -- try it as a stock
            # underlying instead (see resolve_equity_instrument_key()'s
            # docstring for why that's resolved dynamically rather
            # than hardcoded here the way the index keys above are).
            underlying_key = self.resolve_equity_instrument_key(symbol)

        url = f"{BASE_URL}/option/contract"
        resp = request_with_retry("GET", url, headers=self._headers(), timeout=15,
                                   params={"instrument_key": underlying_key},
                                   error_cls=BrokerAPIError)
        contracts = resp.json().get("data", [])

        today = datetime.now(IST).date()
        expiries = sorted({
            datetime.strptime(c["expiry"], "%Y-%m-%d").date()
            for c in contracts if c.get("expiry")
        })
        upcoming = [e for e in expiries if e >= today]
        if not upcoming:
            raise InstrumentNotFound(f"no upcoming expiry found for {symbol}")
        return upcoming[0].isoformat()

    def resolve_equity_instrument_key(self, symbol: str) -> str:
        """
        Resolves `symbol` (e.g. "PNB") to its NSE cash-equity
        instrument_key from the instrument master -- used for STOCK
        underlyings (config.STOCK_SYMBOLS), which have no stable
        hand-typed key the way "NSE_INDEX|Nifty 50" does: an equity's
        instrument_key is tied to its ISIN, and an ISIN is not
        something to hand-copy into source and trust. Resolved fresh
        each call via the same cached _instrument_master() every other
        instrument lookup in this file already uses.

        BUGFIX (live failure, 2026-07-17): matching EQ rows on `name`
        the way derivative rows are matched elsewhere in this file
        (see search_instrument()) does NOT work for equities --
        Upstox's instrument master gives EQ rows a `name` of the full
        listed company name (e.g. "Punjab National Bank"), not the
        short trading symbol, so `row["name"] == "PNB"` never matched
        and every stock alert failed with InstrumentNotFound. Equity
        rows carry the short symbol in `trading_symbol` instead (the
        field name Upstox's own API docs use for this), so that's
        matched first; `name` is kept as a fallback in case a given
        master snapshot varies. Still not independently confirmed
        against a live downloaded file for every symbol in
        STOCK_SYMBOLS -- confirm before trusting a specific one live.
        If more than one row matches (e.g. a same-named BSE listing),
        an NSE-exchange row is preferred where the `exchange` field is
        present.

        Raises InstrumentNotFound if no matching equity row exists.
        """
        master = self._instrument_master()
        matches = [
            row for row in master
            if row.get("instrument_type") == "EQ"
            and (row.get("trading_symbol") == symbol or row.get("name") == symbol)
        ]
        if not matches:
            raise InstrumentNotFound(
                f"no NSE equity instrument_key found for '{symbol}' in the "
                f"instrument master (checked both 'trading_symbol' and "
                f"'name' fields on instrument_type=='EQ' rows) -- confirm "
                f"the symbol is spelled exactly as NSE lists it, and that "
                f"the instrument master actually has an EQ row for it at all"
            )
        for row in matches:
            if str(row.get("exchange", "")).upper().startswith("NSE") and row.get("instrument_key"):
                return row["instrument_key"]
        key = matches[0].get("instrument_key")
        if key:
            return key
        raise InstrumentNotFound(
            f"matched an EQ row for '{symbol}' but it has no instrument_key -- "
            f"inspect the instrument master row directly"
        )

    def resolve_current_month_future(self, symbol: str) -> str:
        """
        Resolves the current-month (nearest unexpired) FUTURES contract
        instrument_key for `symbol` -- so the VWAP futures key never
        needs a manual monthly update again. Rolls forward automatically
        forever: once the current contract's expiry passes, the next
        call simply finds whichever contract is then the nearest
        unexpired one, with zero code changes ever needed for this
        reason.

        Same schema-assumption caveat as search_instrument() above
        (name, expiry, instrument_type, instrument_key field names) --
        confirm against an actual downloaded file. instrument_type for
        futures is assumed to be "FUT", matching Upstox's convention
        (as distinct from "CE"/"PE" for options) -- this part IS
        confirmed live (see BUGFIX below).

        BUGFIX (live failure, 2026-07-17): matching purely on
        `name == symbol` -- which works for NIFTY/BANKNIFTY, where
        "name" already IS the short symbol -- failed live for BOTH
        PNB and CANBK with InstrumentNotFound. At the time, this was
        worked around by ALSO trying `trading_symbol` with an assumed
        "<SYMBOL><DDMMMYY>FUT" suffix convention (e.g. "CANBK26JULFUT"),
        matching rows whose trading_symbol STARTS WITH the plain symbol
        and ENDS WITH "FUT".

        BUGFIX #2 (2026-10-19, production backfill audit, Oct 5-9
        logs): that "<SYMBOL><DDMMMYY>FUT"-suffix assumption was never
        actually confirmed against a live downloaded master, and it was
        WRONG -- confirmed via debug_instrument_masters.py's futures
        diagnostic run against the real file. Upstox's real stock-future
        trading_symbol format is SPACE-SEPARATED with "FUT" as a MIDDLE
        token followed by the expiry, e.g.:

            "KOTAKBANK FUT 29 DEC 26"

        not a suffix like "KOTAKBANK26JULFUT". Because of this, BOTH
        matching conditions were always False for every single stock:
        `trading_symbol.endswith("FUT")` is False (the string ends with
        the two-digit year, e.g. "26"), and `name == symbol` is also
        False (name is the full company name, e.g.
        "KOTAK MAHINDRA BANK LTD", not the short ticker "KOTAKBANK" --
        confirmed from the same live row). The result was a 100%
        InstrumentNotFound failure rate for every stock future's
        resolve_current_month_future() call, confirmed from the Oct 5-9
        production backfill: 155 SYMBOL_FAILED drops / 310 tracebacks,
        all from this single root cause (Upstox resolution fails ->
        AngelOne single-call fallback -> AngelOne 403 rate-limited on
        ~16% of those fallback attempts -> full symbol drop for that
        cycle). KOTAKBANK alone accounted for 113 InstrumentNotFound
        occurrences and 18 SYMBOL_FAILED drops in that window.

        Fixed: trading_symbol is now split on whitespace and matched by
        TOKEN POSITION -- the first token must equal `symbol` exactly
        and the second token must be exactly "FUT" (not a substring/
        suffix check). This correctly matches
        "KOTAKBANK FUT 29 DEC 26" (tokens: ["KOTAKBANK", "FUT", "29",
        "DEC", "26"]) and still correctly matches NIFTY/BANKNIFTY via
        the pre-existing `name == symbol` path (their `name` field
        already IS the short index symbol, unconditionally
        unaffected by this change).

        Raises InstrumentNotFound if no unexpired futures contract for
        `symbol` exists in the instrument master -- shouldn't happen
        for NIFTY/BANKNIFTY or any symbol with current F&O eligibility
        under normal circumstances; if it does now, the instrument
        master is genuinely stale/missing that contract, or that
        specific symbol has lost F&O eligibility -- not a further
        field-name mismatch (that class of bug is what this fix
        resolved).
        """
        master = self._instrument_master()
        today = datetime.now(IST).date()
        candidates = []
        for row in master:
            if row.get("instrument_type") != "FUT":
                continue
            trading_symbol = str(row.get("trading_symbol") or "")
            name_matches = row.get("name") == symbol
            # BUGFIX #2 (2026-10-19): token-position match, not
            # substring/suffix -- see docstring above for the real
            # Upstox format ("KOTAKBANK FUT 29 DEC 26") that broke the
            # old startswith(...)+endswith("FUT") check 100% of the
            # time for every stock.
            ts_tokens = trading_symbol.split()
            trading_symbol_matches = (
                len(ts_tokens) >= 2
                and ts_tokens[0] == symbol
                and ts_tokens[1] == "FUT"
            )
            if not (name_matches or trading_symbol_matches):
                continue
            expiry_raw = row.get("expiry")
            if not expiry_raw:
                continue
            try:
                if isinstance(expiry_raw, str):
                    expiry_date = datetime.fromisoformat(expiry_raw).date()
                else:
                    # Upstox's instrument-master JSON (NSE.json.gz) gives
                    # F&O "expiry" as epoch MILLISECONDS (int/float), not
                    # an ISO string -- unlike the /v2/option/contract API
                    # used in get_current_expiry(), which does return
                    # "YYYY-MM-DD" strings. That mismatch is what caused
                    # the "argument must be str" crash: every row's real
                    # expiry values are ms timestamps, so fromisoformat()
                    # was never going to work on them.
                    expiry_date = datetime.fromtimestamp(
                        expiry_raw / 1000, tz=IST
                    ).date()
            except (ValueError, TypeError, OSError):
                # Malformed/unexpected value on this one row -- skip it,
                # don't let it take down the whole scan.
                continue
            if expiry_date >= today:
                candidates.append((expiry_date, row.get("instrument_key")))

        if not candidates:
            raise InstrumentNotFound(
                f"no unexpired futures contract found for {symbol} in the "
                f"instrument master -- master may be stale, 'instrument_type'/"
                f"'expiry' field assumptions above are wrong for your account "
                f"segment, or {symbol} genuinely has no current F&O futures "
                f"contract (lost eligibility)"
            )
        candidates.sort(key=lambda pair: pair[0])
        return candidates[0][1]  # nearest (current-month) unexpired contract
