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
"""

import gzip
import json
from datetime import datetime, timezone, timedelta

from broker import Broker, drop_incomplete_trailing_candle
from auth import get_upstox_token
from retry_utils import request_with_retry
from exceptions import BrokerAPIError, InstrumentNotFound
from cache import TTLCache

IST = timezone(timedelta(hours=5, minutes=30))
BASE_URL = "https://api.upstox.com/v2"
BASE_URL_V3 = "https://api.upstox.com/v3"
INSTRUMENT_MASTER_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"

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
}


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
                entry[prefix + "bid"] = market.get("bid_price", 0.0)
                entry[prefix + "ask"] = market.get("ask_price", 0.0)
                greeks = side.get("option_greeks") or {}
                entry[prefix + "delta"] = greeks.get("delta")
                entry[prefix + "iv"] = greeks.get("iv")
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
        return json.loads(gzip.decompress(resp.content))

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

        Schema assumption -- UNVERIFIED against a live downloaded
        file, same caveat as search_instrument()/
        resolve_current_month_future() above: equity rows have
        instrument_type == "EQ" and their "name" field equals the
        plain NSE trading symbol (e.g. "PNB"). Confirm this before
        depending on it live; if the assumption is wrong this raises
        InstrumentNotFound rather than silently resolving to the
        wrong instrument.

        Raises InstrumentNotFound if no matching equity row exists.
        """
        master = self._instrument_master()
        for row in master:
            if row.get("name") == symbol and row.get("instrument_type") == "EQ":
                key = row.get("instrument_key")
                if key:
                    return key
        raise InstrumentNotFound(
            f"no NSE equity instrument_key found for '{symbol}' in the "
            f"instrument master -- confirm the symbol is spelled exactly "
            f"as NSE lists it, and that the 'name'/'instrument_type' "
            f"field assumptions above are correct for equity rows"
        )

    def resolve_current_month_future(self, symbol: str) -> str:
        """
        Resolves the current-month (nearest unexpired) FUTURES contract
        instrument_key for `symbol` ("NIFTY" or "BANKNIFTY") -- so the
        VWAP futures key never needs a manual monthly update again.
        Rolls forward automatically forever: once the current contract's
        expiry passes, the next call simply finds whichever contract is
        then the nearest unexpired one, with zero code changes ever
        needed for this reason.

        Same schema-assumption caveat as search_instrument() above
        (name, expiry, instrument_type, instrument_key field names) --
        confirm against an actual downloaded file. instrument_type for
        futures is assumed to be "FUT", matching Upstox's convention
        (as distinct from "CE"/"PE" for options).

        Raises InstrumentNotFound if no unexpired futures contract for
        `symbol` exists in the instrument master -- shouldn't happen
        for NIFTY/BANKNIFTY under normal circumstances; if it does, the
        instrument master is stale or a schema assumption above is wrong.
        """
        master = self._instrument_master()
        today = datetime.now(IST).date()
        candidates = []
        for row in master:
            if row.get("name") != symbol or row.get("instrument_type") != "FUT":
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
                f"instrument master -- master may be stale, or 'name'/"
                f"'instrument_type'/'expiry' field assumptions above are wrong "
                f"for your account segment"
            )
        candidates.sort(key=lambda pair: pair[0])
        return candidates[0][1]  # nearest (current-month) unexpired contract
