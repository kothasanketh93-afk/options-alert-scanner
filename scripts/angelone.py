"""
angelone.py
-----------
Concrete Broker implementation for Angel One SmartAPI. This is the
only file that should know Angel One's specific endpoint paths,
request/response shapes, or instrument-token format -- everything
else talks to the Broker interface in broker.py.

AUTHENTICATION: fully automated, no manual TOTP entry, no browser.
Angel One's login is a direct POST (clientcode + PIN + a 6-digit TOTP
code) -- unlike Upstox's OAuth-redirect flow, this needs no headless
browser simulation, so it's safe to script against Angel One's own
official endpoint rather than working around a browser-only flow. See
auth.py's get_angelone_credentials()["totp_now"] for the pyotp code
generation.

ON "SESSION REFRESH": this project runs as a fresh process per GitHub
Actions job (5-15 minutes apart), not a long-lived one -- so there is
no mid-run session to refresh. Logging in once at the start of every
run via TOTP already gives every run a fresh session; that IS the
"automatic session refresh" this file needs to provide. (Angel One
does issue a refreshToken, but calling generateTokens() with it
returns a new JWT carrying the SAME expiry as the original -- it does
not extend the trading day -- confirmed on Angel's own SmartAPI forum.
So a refresh token would only matter for a process that stays alive
for many hours without restarting, which this one never does.)

RETRY-ONCE-THEN-FAIL: login() retries once on a login failure (network
blip, transient 5xx) before giving up -- see login()'s docstring.
Giving up here means raising, which broker_failover.py's FailoverBroker
catches to switch to Upstox -- this file has no knowledge of Upstox or
of failover, by design (same separation broker.py's docstring
describes for every concrete broker).

VERIFY BEFORE RELYING ON THIS IN PRODUCTION (same disclaimer as
upstox.py): endpoint paths/params below match Angel One's documented
SmartAPI shapes as of this writing (smartapi.angelone.in/docs), cross-
checked against their own forum threads and the smartapi-python SDK's
source where the docs page didn't have example responses. Two
specific things flagged for you to confirm against your own account
before trading real money on this:

  1. OpenAPIScripMaster.json's "strike" field -- some historical
     Angel One scrip master snapshots stored strike prices multiplied
     by 100 (e.g. NIFTY 24500 CE as "2450000.000000") while others
     store it as a plain float ("24500.000000"). search_instrument()
     below AUTO-DETECTS this per-file (comparing against the strike
     step you expect) rather than hardcoding one convention -- but
     confirm the detection actually fires correctly against a fresh
     download before trusting it unattended.
  2. Option Greeks (delta) -- NOW WIRED IN via
     _fetch_option_greeks()/POST .../marketData/v1/optionGreek, per
     Angel One's own SmartAPI forum announcement (schema confirmed
     from real request/response examples posted there -- this specific
     endpoint isn't listed on the main smartapi.angelone.in docs site
     as of this writing, only announced on the forum, so treat it as
     slightly less officially-documented than the other endpoints in
     this file and confirm one live call against your own account
     before trusting it unattended). get_option_chain() below now
     populates a real "delta" (plus "gamma"/"theta"/"vega"/"iv" as
     bonus fields, matching Upstox's extras) for every strike instead
     of hardcoding None. Known limitation reported on the forum: for
     an underlying with both a weekly and monthly expiry (e.g. NIFTY),
     some users have seen this endpoint return the MONTHLY contract's
     greeks even when a weekly expirydate was requested -- unresolved
     by Angel One as of this writing. If your setups are on the weekly
     expiry, spot-check a live response against another source before
     relying on the delta value unattended. Degrades gracefully (not a
     hard failure) if Angel One reports "no data" for a given
     name+expiry -- see _fetch_option_greeks()'s docstring.
"""

import time
import requests
from datetime import datetime, timezone, timedelta

from broker import Broker, drop_incomplete_trailing_candle
from auth import get_angelone_credentials
from retry_utils import request_with_retry
from exceptions import BrokerAPIError, InstrumentNotFound, AuthenticationError
from cache import TTLCache
from logging_setup import get_logger
from config import STOCK_SYMBOLS, ANGELONE_HISTORICAL_MIN_INTERVAL_SECONDS

IST = timezone(timedelta(hours=5, minutes=30))
BASE_URL = "https://apiconnect.angelone.in"
SCRIP_MASTER_URL = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
log = get_logger("api")

# Angel One's historical-candle interval enum -- distinct strings from
# Upstox's ("1minute" etc.), translated here so scanner.py/market_data.py
# keep using the SAME interval names everywhere regardless of which
# broker is actually serving the call (see broker.py's docstring).
_INTERVAL_MAP = {
    "1minute": "ONE_MINUTE",
    "5minute": "FIVE_MINUTE",
    "15minute": "FIFTEEN_MINUTE",
}

# Required on every Angel One request, including login -- these are
# client-identification headers, not auth. Angel One's own docs/forum
# confirm placeholder values are widely used and accepted for
# server-side (non-browser, non-mobile) API clients like this one;
# they are NOT a substitute for the Authorization/X-PrivateKey headers
# that actually authenticate the request.
_CLIENT_ID_HEADERS = {
    "X-UserType": "USER",
    "X-SourceID": "WEB",
}

_cached_public_ip = None
_last_historical_call_ts = 0.0


def _throttle_historical_call():
    """
    Enforces ANGELONE_HISTORICAL_MIN_INTERVAL_SECONDS between
    consecutive getCandleData calls -- see that config constant's
    comment for why: retrying after being rate-limited wasn't enough,
    since the limit is sustained across a whole run's worth of calls,
    not a one-off burst. Sleeps only as much as needed to reach the
    minimum spacing; a call that's already naturally slower than the
    interval (e.g. after a retry's own backoff) doesn't sleep at all.
    """
    global _last_historical_call_ts
    elapsed = time.time() - _last_historical_call_ts
    remaining = ANGELONE_HISTORICAL_MIN_INTERVAL_SECONDS - elapsed
    if remaining > 0:
        time.sleep(remaining)
    _last_historical_call_ts = time.time()


def _detect_public_ip() -> str:
    """
    Every request to Angel One previously sent X-ClientPublicIP:
    "127.0.0.1" -- a hardcoded loopback address, obviously not this
    runner's real IP. This was tried as a fix for the getCandleData
    400/403 failures (2026-07-21) but turned out NOT to be the actual
    cause -- the real cause was a sustained rate limit on that
    endpoint (see ANGELONE_HISTORICAL_MIN_INTERVAL_SECONDS in
    config.py and _throttle_historical_call() below). Kept anyway as
    reasonable hygiene -- sending a real public IP is still more
    correct than a hardcoded loopback -- but don't expect this
    function alone to fix a rate-limit-flavored failure.

    Looks up this machine's real outbound public IP via a plain HTTP
    GET to a public IP-echo service (no auth needed) and caches it for
    the process's lifetime -- it won't change mid-run on a GitHub
    Actions runner. Falls back to the old loopback placeholder (with a
    logged warning, not a crash) if every lookup attempt fails, so a
    network hiccup here degrades back to prior (known-flaky) behavior
    rather than blocking the whole broker.

    """
    global _cached_public_ip
    if _cached_public_ip:
        return _cached_public_ip
    for url in ("https://api.ipify.org", "https://checkip.amazonaws.com"):
        try:
            resp = requests.get(url, timeout=5)
            resp.raise_for_status()
            ip = resp.text.strip()
            if ip:
                _cached_public_ip = ip
                return ip
        except Exception:
            continue
    log.warning(
        "Could not detect real public IP for Angel One request headers -- "
        "falling back to 127.0.0.1, which may itself cause getCandleData "
        "to be rejected (see _detect_public_ip()'s docstring)."
    )
    _cached_public_ip = "127.0.0.1"
    return _cached_public_ip


class AngelOneBroker(Broker):
    def __init__(self):
        self._instrument_cache = TTLCache(ttl_seconds=3600)
        self._chain_cache = TTLCache(ttl_seconds=120)
        # Same TTL as _chain_cache -- Greeks are fetched once per
        # (name, expiry) and reused for every strike in that chain
        # within the window, same reasoning as the chain cache itself.
        self._greeks_cache = TTLCache(ttl_seconds=120)
        self._jwt_token = None
        self._feed_token = None
        self._logged_in_at = None

    # -----------------------------------------------------------------
    # Authentication
    # -----------------------------------------------------------------

    def _headers(self, authenticated: bool = True) -> dict:
        creds = get_angelone_credentials()
        headers = dict(_CLIENT_ID_HEADERS)
        public_ip = _detect_public_ip()
        headers["X-ClientLocalIP"] = public_ip  # no real LAN IP available on a CI runner; using the same real public IP is closer to genuine than a hardcoded loopback
        headers["X-ClientPublicIP"] = public_ip
        headers["X-MACAddress"] = "00:00:00:00:00:00"  # still a placeholder -- unlike the IP fields, no live-failure evidence points at this one yet
        headers["X-PrivateKey"] = creds["api_key"]
        headers["Content-Type"] = "application/json"
        headers["Accept"] = "application/json"
        if authenticated:
            if self._jwt_token is None:
                self.login()
            headers["Authorization"] = f"Bearer {self._jwt_token}"
        return headers

    def login(self, retry_once: bool = True) -> None:
        """
        Logs in with clientcode + PIN + a freshly-generated TOTP code
        (see auth.get_angelone_credentials()["totp_now"]) -- fully
        automated, no manual entry, no browser. Called lazily by the first authenticated
        call this run (via _headers()); you don't need to call this
        yourself except in a health-check probe.

        Retries ONCE on any failure (network blip, transient 5xx, a
        TOTP code generated right at its 30-second boundary rejected
        as stale by Angel's clock skew tolerance) before raising --
        matching the spec's "retry authentication once if login
        fails." request_with_retry() already handles connection
        errors/5xx/429 with its own backoff; this outer retry_once is
        specifically for a clean SECOND ATTEMPT with a freshly
        regenerated TOTP code, since a code that failed once (e.g. it
        expired between generation and the server receiving it) will
        just fail again identically without a new code.

        Raises AuthenticationError if both attempts fail -- caller
        (broker_failover.py) catches this to switch to Upstox.
        """
        creds = get_angelone_credentials()
        totp_code = creds["totp_now"]()

        url = f"{BASE_URL}/rest/auth/angelbroking/user/v1/loginByPassword"
        body = {
            "clientcode": creds["client_code"],
            "password": creds["pin"],
            "totp": totp_code,
        }
        try:
            resp = request_with_retry(
                "POST", url, json=body, headers=self._headers(authenticated=False),
                timeout=10, error_cls=BrokerAPIError,
            )
        except (BrokerAPIError, AuthenticationError):
            if not retry_once:
                raise
            time.sleep(1.5)  # let a boundary-expired TOTP code roll over
            self.login(retry_once=False)
            return

        payload = resp.json()
        if not payload.get("status"):
            # Angel One returns HTTP 200 with status=False for a
            # rejected login (bad PIN, bad TOTP, account issue) --
            # NOT an HTTP 401/403, so request_with_retry's own
            # AUTH_FAILURE_CODES handling never sees this. Must check
            # the response body explicitly.
            message = payload.get("message", "login rejected")
            if retry_once:
                time.sleep(1.5)
                self.login(retry_once=False)
                return
            raise AuthenticationError(f"Angel One login failed: {message}")

        data = payload["data"]
        self._jwt_token = data["jwtToken"]
        self._feed_token = data.get("feedToken")
        self._logged_in_at = datetime.now(IST)

    # -----------------------------------------------------------------
    # Broker interface
    # -----------------------------------------------------------------

    def get_candles(self, instrument_key: str, interval: str, since_iso: str = None) -> list:
        """
        instrument_key here is "EXCHANGE|SYMBOLTOKEN" (e.g.
        "NFO|66522") -- Angel One's candle API needs the exchange
        segment and the numeric token as separate fields, unlike
        Upstox's single opaque instrument_key string, so this file's
        instrument_key format is broker-specific by necessity (see
        search_instrument()/resolve_current_month_future() below,
        which both produce keys in this exact format).
        """
        if interval not in _INTERVAL_MAP:
            raise BrokerAPIError(f"unsupported interval: {interval}")
        _throttle_historical_call()
        exchange, token = _split_instrument_key(instrument_key)

        since_dt = None
        if since_iso:
            since_dt = datetime.fromisoformat(since_iso)
            if since_dt.tzinfo is None:
                since_dt = since_dt.replace(tzinfo=IST)
            from_dt = since_dt
        else:
            from_dt = datetime.now(IST).replace(hour=9, minute=15, second=0, microsecond=0)
        to_dt = datetime.now(IST)

        url = f"{BASE_URL}/rest/secure/angelbroking/historical/v1/getCandleData"
        body = {
            "exchange": exchange,
            "symboltoken": token,
            "interval": _INTERVAL_MAP[interval],
            "fromdate": from_dt.strftime("%Y-%m-%d %H:%M"),
            "todate": to_dt.strftime("%Y-%m-%d %H:%M"),
        }
        resp = request_with_retry("POST", url, json=body, headers=self._headers(),
                                   timeout=15, error_cls=BrokerAPIError)
        payload = resp.json()
        if not payload.get("status"):
            raise BrokerAPIError(f"getCandleData rejected: {payload.get('message')}")

        out = []
        for c in payload.get("data") or []:
            # [timestamp, open, high, low, close, volume] -- timestamp
            # comes back with a timezone offset already (e.g.
            # "2026-07-13T09:15:00+05:30").
            ts = datetime.fromisoformat(c[0])
            if since_dt is not None and ts <= since_dt:
                continue
            out.append({
                "timestamp": ts.isoformat(),
                "open": float(c[1]),
                "high": float(c[2]),
                "low": float(c[3]),
                "close": float(c[4]),
                "volume": float(c[5]) if len(c) > 5 and c[5] is not None else 0.0,
            })
        out.sort(key=lambda c: c["timestamp"])
        return drop_incomplete_trailing_candle(out, interval)

    def get_ltp(self, instrument_key: str) -> float:
        exchange, token = _split_instrument_key(instrument_key)
        trading_symbol = self._token_to_symbol(exchange, token)

        url = f"{BASE_URL}/rest/secure/angelbroking/order/v1/getLtpData"
        body = {"exchange": exchange, "tradingsymbol": trading_symbol, "symboltoken": token}
        resp = request_with_retry("POST", url, json=body, headers=self._headers(),
                                   timeout=10, error_cls=BrokerAPIError)
        payload = resp.json()
        if not payload.get("status") or not payload.get("data"):
            raise InstrumentNotFound(f"no LTP returned for {instrument_key}")
        return float(payload["data"]["ltp"])

    def get_option_chain(self, underlying_key: str, expiry_date: str) -> list:
        """
        Built from the scrip master (to enumerate CE/PE strikes for
        this underlying+expiry) plus a batch quote call for live
        oi/volume/bid/ask/ltp, plus one Option Greeks call for
        delta/gamma/theta/vega/iv -- Angel One doesn't expose a single
        "option chain" endpoint the way Upstox does, so this is
        assembled from three calls instead of one.

        "delta" (and gamma/theta/vega/iv) come from
        _fetch_option_greeks() -- see this file's module docstring,
        point 2, including a known weekly-vs-monthly-expiry caveat
        reported on Angel One's forum. Any of these can still be None
        for a given strike if the Greeks endpoint had no data for it
        (illiquid strike, off-hours) -- delta_acceptable()/
        expiry_theta_ok() in scanner.py already reject that specific
        setup when delta is None, same as before this was wired in.
        """
        cache_key = (underlying_key, expiry_date)
        cached = self._chain_cache.get(cache_key)
        if cached is not None:
            return cached

        name = _underlying_name(underlying_key)
        master = self._scrip_master()
        # expiry_date arrives as ISO -- same BUGFIX as search_instrument()
        # above: convert to the scrip master's native "DDMMMYYYY" format
        # before matching rows, or every strike lookup silently returns
        # nothing and this raises InstrumentNotFound below.
        native_expiry = _iso_to_native_expiry(expiry_date)
        option_instrument_type = _option_instrument_type(name)
        strike_divisor = _detect_strike_divisor(master, name, native_expiry, option_instrument_type)

        rows_by_strike = {}
        for row in master:
            if (row.get("name") != name or row.get("expiry") != native_expiry
                    or row.get("instrumenttype") != option_instrument_type):
                continue
            try:
                strike = float(row["strike"]) / strike_divisor
            except (KeyError, ValueError):
                continue
            option_type = row.get("symbol", "")[-2:]  # tradingsymbol ends CE/PE
            if option_type not in ("CE", "PE"):
                continue
            rows_by_strike.setdefault(strike, {})[option_type] = row

        if not rows_by_strike:
            raise InstrumentNotFound(
                f"no option chain rows found for {name} expiry {expiry_date} in the "
                f"scrip master -- wrong expiry format, wrong 'name', or the master is stale"
            )

        # Batch-quote every token in groups of <=50 per Angel One's
        # documented /market/v1/quote/ limit.
        all_tokens = [row["token"] for pair in rows_by_strike.values() for row in pair.values()]
        quotes_by_token = self._batch_quotes("NFO", all_tokens)

        # One Greeks call covers every strike for this name+expiry (see
        # module docstring, point 2) -- fetched once here, not per-strike.
        # _fetch_option_greeks() documents its expiry_date param as the
        # scrip master's native format, so pass native_expiry, not the
        # ISO expiry_date this method received.
        greeks_by_strike = self._fetch_option_greeks(name, native_expiry)

        chain = []
        for strike, pair in sorted(rows_by_strike.items()):
            entry = {"strike_price": strike}
            for option_type, key_name in (("CE", "call_instrument_key"), ("PE", "put_instrument_key")):
                row = pair.get(option_type)
                if not row:
                    continue
                token = row["token"]
                entry[key_name] = f"NFO|{token}"
                quote = quotes_by_token.get(token, {})
                prefix = "call_" if option_type == "CE" else "put_"
                entry[prefix + "oi"] = quote.get("opnInterest", 0.0)
                entry[prefix + "volume"] = quote.get("tradeVolume", 0.0)
                depth = quote.get("depth", {})
                buy = depth.get("buy") or [{}]
                sell = depth.get("sell") or [{}]
                entry[prefix + "bid"] = buy[0].get("price", 0.0)
                entry[prefix + "ask"] = sell[0].get("price", 0.0)
                entry[prefix + "ltp"] = quote.get("ltp", 0.0)

                greeks = greeks_by_strike.get((round(strike, 2), option_type), {})
                entry[prefix + "delta"] = greeks.get("delta")  # None if unavailable -- see module docstring
                entry[prefix + "gamma"] = greeks.get("gamma")
                entry[prefix + "theta"] = greeks.get("theta")
                entry[prefix + "vega"] = greeks.get("vega")
                entry[prefix + "iv"] = greeks.get("iv")
            chain.append(entry)

        self._chain_cache.set(cache_key, chain)
        return chain

    def _fetch_option_greeks(self, name: str, expiry_date: str) -> dict:
        """
        Returns {(strike_price_float, "CE"|"PE"): {"delta", "gamma",
        "theta", "vega", "iv"}} for every strike Angel One's Option
        Greeks endpoint has data for, given `name` ("NIFTY"/"BANKNIFTY")
        and `expiry_date` in the SAME "DDMMMYYYY" format already used
        everywhere else in this file (e.g. "17JUL2025") -- the scrip
        master's own "expiry" field format, which is exactly what
        get_option_chain()'s caller already passes in.

        Endpoint confirmed from Angel One's own SmartAPI forum
        announcement and real request/response examples posted there
        (POST .../marketData/v1/optionGreek, body {"name", "expirydate"},
        response data is a flat list of per-strike/per-side rows) --
        this endpoint isn't listed on the main smartapi.angelone.in docs
        site as of this writing, only announced on the forum, so it's
        slightly less officially-documented than this file's other
        endpoints. See module docstring point 2 for a known caveat
        (weekly vs monthly expiry) reported by other users.

        Degrades gracefully to {} (NOT a raised exception) when Angel
        One reports status=false with "no data available" for this
        name+expiry (errorcode AB9019 as of this writing) -- this is a
        routine, expected response outside live market hours or for a
        contract with no Greeks published yet, not a broker health
        problem. Every downstream strike just sees delta=None the same
        as before this was wired in, which quality_gate() already
        handles by rejecting that specific setup -- it must NOT raise
        and take down the whole option-chain fetch (and trip the
        failover streak) over what is normal off-hours behavior. A
        genuine network/5xx/auth failure still raises via
        request_with_retry, same as every other call in this file --
        only the "no data" response body is treated specially.
        """
        cache_key = (name, expiry_date)
        cached = self._greeks_cache.get(cache_key)
        if cached is not None:
            return cached

        url = f"{BASE_URL}/rest/secure/angelbroking/marketData/v1/optionGreek"
        body = {"name": name, "expirydate": expiry_date}
        resp = request_with_retry("POST", url, json=body, headers=self._headers(),
                                   timeout=15, error_cls=BrokerAPIError)
        payload = resp.json()
        if not payload.get("status"):
            log.warning(
                "optionGreek: no data for %s %s (errorcode %s: %s) -- "
                "delta/theta gates will see None for every strike this call.",
                name, expiry_date, payload.get("errorcode"), payload.get("message"),
            )
            self._greeks_cache.set(cache_key, {})
            return {}

        greeks = {}
        for row in payload.get("data") or []:
            try:
                strike = round(float(row["strikePrice"]), 2)
            except (KeyError, ValueError, TypeError):
                continue
            option_type = row.get("optionType")
            if option_type not in ("CE", "PE"):
                continue
            greeks[(strike, option_type)] = {
                "delta": _safe_float(row.get("delta")),
                "gamma": _safe_float(row.get("gamma")),
                "theta": _safe_float(row.get("theta")),
                "vega": _safe_float(row.get("vega")),
                "iv": _safe_float(row.get("impliedVolatility")),
            }
        self._greeks_cache.set(cache_key, greeks)
        return greeks

    def _batch_quotes(self, exchange: str, tokens: list) -> dict:
        """Returns {token: quote_dict} for every token, chunked into
        groups of 50 (Angel One's documented per-request limit)."""
        by_token = {}
        for i in range(0, len(tokens), 50):
            chunk = tokens[i:i + 50]
            url = f"{BASE_URL}/rest/secure/angelbroking/market/v1/quote/"
            body = {"mode": "FULL", "exchangeTokens": {exchange: chunk}}
            resp = request_with_retry("POST", url, json=body, headers=self._headers(),
                                       timeout=15, error_cls=BrokerAPIError)
            payload = resp.json()
            if not payload.get("status"):
                raise BrokerAPIError(f"quote request rejected: {payload.get('message')}")
            for item in (payload.get("data") or {}).get("fetched", []):
                by_token[item["symbolToken"]] = item
        return by_token

    def _token_to_symbol(self, exchange: str, token: str) -> str:
        for row in self._scrip_master():
            if row.get("token") == token and row.get("exch_seg") == exchange:
                return row["symbol"]
        raise InstrumentNotFound(f"token {token} not found on {exchange} in scrip master")

    def _scrip_master(self) -> list:
        return self._instrument_cache.get_or_set("master", self._fetch_scrip_master)

    def _fetch_scrip_master(self) -> list:
        # Public JSON file, no auth needed, updated daily ~8:30 AM IST
        # per Angel One's own forum. Large file (tens of MB) -- the
        # TTLCache above means this is fetched at most once per run.
        resp = request_with_retry("GET", SCRIP_MASTER_URL, timeout=60, error_cls=BrokerAPIError)
        return resp.json()

    def download_instrument_master(self) -> list:
        """
        Phase 2 (instrument_master.py). Normalizes the SAME cached raw
        scrip master _scrip_master() already provides (no extra
        download) into the broker-agnostic row shape documented on
        Broker.download_instrument_master() in broker.py.

        Two Angel-One-specific corrections applied here so
        instrument_master.py never has to know about them:

        1. Expiry: native "DDMMMYYYY" (e.g. "17JUL2025") converted to
           ISO via the same _iso_to_native_expiry()-inverse logic used
           elsewhere in this file.
        2. Strike scaling: this scrip master sometimes stores strikes
           x100 (see this file's module docstring, point 1, and
           _detect_strike_divisor()'s docstring). Unlike
           _detect_strike_divisor() (which samples fresh from the full
           master for ONE symbol+expiry+instrumenttype at a time --
           fine for a single lookup, too slow to call per-group across
           the WHOLE master here), this method groups rows by
           (name, expiry, instrumenttype) in a single pass and applies
           the same magnitude heuristic (average sample strike >
           100,000 => scaled by 100) per group. Same caveat as
           _detect_strike_divisor()'s docstring: calibrated for
           index-scale strikes -- a lower-priced stock scaled by 100
           could still land under the 100,000 cutoff and be misread as
           unscaled. instrument_master.validate_symbol() logs strike
           counts precisely so an obviously-wrong divisor (e.g. every
           strike for a stock coming out at a wildly unrealistic price)
           is visible in the logs rather than silently wrong.
        """
        master = self._scrip_master()

        # Pass 1: collect up to 20 sample strikes per (name, expiry,
        # instrumenttype) group, options rows only -- futures/EQ rows
        # have no strike to scale.
        samples = {}
        for row in master:
            if row.get("instrumenttype") not in ("OPTIDX", "OPTSTK"):
                continue
            key = (row.get("name"), row.get("expiry"), row.get("instrumenttype"))
            bucket = samples.setdefault(key, [])
            if len(bucket) < 20 and row.get("strike"):
                try:
                    bucket.append(float(row["strike"]))
                except (TypeError, ValueError):
                    pass

        divisor_by_group = {}
        for key, strikes in samples.items():
            if not strikes:
                divisor_by_group[key] = 1.0
                continue
            avg_strike = sum(strikes) / len(strikes)
            divisor_by_group[key] = 100.0 if avg_strike > 100000 else 1.0

        normalized = []
        for row in master:
            token = row.get("token")
            exch_seg = row.get("exch_seg")
            if not token or not exch_seg:
                continue  # unusable row -- can't build an instrument_key
            instrument_key = f"{exch_seg}|{token}"
            raw_type = row.get("instrumenttype")
            name = row.get("name")
            trading_symbol = row.get("symbol")

            option_type = None
            if raw_type in ("OPTIDX", "OPTSTK") and trading_symbol:
                suffix = trading_symbol[-2:]
                option_type = suffix if suffix in ("CE", "PE") else None

            contract_class = {
                "OPTIDX": "INDEX_OPTION", "OPTSTK": "STOCK_OPTION",
                "FUTIDX": "INDEX_FUTURE", "FUTSTK": "STOCK_FUTURE",
                "EQ": "EQUITY",
            }.get(raw_type, raw_type or "UNKNOWN")

            expiry_native = row.get("expiry")
            expiry_iso = None
            if expiry_native:
                try:
                    expiry_iso = datetime.strptime(expiry_native, "%d%b%Y").date().isoformat()
                except ValueError:
                    expiry_iso = None

            strike_price = None
            if raw_type in ("OPTIDX", "OPTSTK") and row.get("strike"):
                try:
                    raw_strike = float(row["strike"])
                    divisor = divisor_by_group.get((name, expiry_native, raw_type), 1.0)
                    strike_price = raw_strike / divisor
                except (TypeError, ValueError):
                    strike_price = None

            lot_size = row.get("lotsize")
            try:
                lot_size = int(float(lot_size)) if lot_size not in (None, "") else None
            except (TypeError, ValueError):
                lot_size = None

            normalized.append({
                "symbol": trading_symbol or name,
                "underlying_symbol": name,
                "exchange": exch_seg,
                "contract_class": contract_class,
                "option_type": option_type,
                "expiry": expiry_iso,
                "strike_price": strike_price,
                "lot_size": lot_size,
                "trading_symbol": trading_symbol,
                "instrument_key": instrument_key,
            })
        return normalized

    def get_daily_candles(self, instrument_key: str, count: int) -> list:
        """
        Phase 1 (opportunity_score.py). Reuses getCandleData (same
        endpoint get_candles() above uses) with interval="ONE_DAY" --
        Angel One's historical API supports daily bars on the exact
        same endpoint, unlike Upstox where it's a distinct URL. `count`
        is approximate calendar-day lookback, same trimming approach
        as UpstoxBroker.get_daily_candles() -- see that method's
        docstring for why.
        """
        _throttle_historical_call()
        exchange, token = _split_instrument_key(instrument_key)
        to_dt = datetime.now(IST).date() - timedelta(days=1)
        from_dt = to_dt - timedelta(days=int(count * 1.6) + 5)

        url = f"{BASE_URL}/rest/secure/angelbroking/historical/v1/getCandleData"
        body = {
            "exchange": exchange, "symboltoken": token, "interval": "ONE_DAY",
            "fromdate": from_dt.strftime("%Y-%m-%d 00:00"),
            "todate": to_dt.strftime("%Y-%m-%d 23:59"),
        }
        resp = request_with_retry("POST", url, json=body, headers=self._headers(),
                                   timeout=15, error_cls=BrokerAPIError)
        payload = resp.json()
        if not payload.get("status"):
            raise BrokerAPIError(f"getCandleData (ONE_DAY) rejected: {payload.get('message')}")

        out = []
        for c in payload.get("data") or []:
            ts = datetime.fromisoformat(c[0])
            out.append({
                "timestamp": ts.isoformat(), "open": float(c[1]), "high": float(c[2]),
                "low": float(c[3]), "close": float(c[4]),
                "volume": float(c[5]) if len(c) > 5 and c[5] is not None else 0.0,
            })
        out.sort(key=lambda c: c["timestamp"])
        return out[-count:] if len(out) > count else out

    def get_batch_quotes(self, instrument_keys: list) -> dict:
        """
        Phase 1. Angel One's Market Quote API (quote/v1/) accepts a
        batch of {exchange, symboltoken} pairs grouped by exchange --
        this groups the "EXCHANGE|token"-format instrument_keys this
        file uses everywhere else by exchange before calling, since the
        API needs them pre-grouped. Chunked at 50 tokens/exchange/call
        (documented Angel One limit as of this file's other endpoints
        -- confirm against current API docs before relying on this
        exact number, same "verify before trusting" caveat as this
        file's other broker-specific magic numbers).
        """
        by_exchange = {}
        for key in instrument_keys:
            exchange, token = _split_instrument_key(key)
            by_exchange.setdefault(exchange, []).append(token)

        out = {}
        url = f"{BASE_URL}/rest/secure/angelbroking/market/v1/quote/"
        for exchange, tokens in by_exchange.items():
            for i in range(0, len(tokens), 50):
                chunk = tokens[i:i + 50]
                body = {"mode": "FULL", "exchangeTokens": {exchange: chunk}}
                resp = request_with_retry("POST", url, json=body, headers=self._headers(),
                                           timeout=15, error_cls=BrokerAPIError)
                payload = resp.json()
                if not payload.get("status"):
                    raise BrokerAPIError(f"quote rejected: {payload.get('message')}")
                for row in payload.get("data", {}).get("fetched", []):
                    key = f"{exchange}|{row.get('symbolToken')}"
                    out[key] = {
                        "ltp": float(row.get("ltp", 0) or 0),
                        "prev_close": float(row.get("close", 0) or 0),
                        "volume": float(row.get("tradeVolume", 0) or 0),
                    }
        return out

    def search_instrument(self, symbol: str, expiry_date: str, strike: float, option_type: str) -> str:
        master = self._scrip_master()
        name = symbol  # Angel One's scrip master "name" field matches NIFTY/BANKNIFTY directly
        # expiry_date arrives as ISO ("YYYY-MM-DD") per broker.py's
        # interface contract -- the scrip master stores it natively as
        # "DDMMMYYYY" (e.g. "17JUL2025"), so it must be converted
        # before comparing against row["expiry"] below. BUGFIX: this
        # used to compare the ISO string directly against the native
        # format, which could never match -- every Angel One
        # search_instrument() call would have raised InstrumentNotFound.
        native_expiry = _iso_to_native_expiry(expiry_date)
        option_instrument_type = _option_instrument_type(name)
        divisor = _detect_strike_divisor(master, name, native_expiry, option_instrument_type)
        target = round(strike * divisor)
        for row in master:
            if (row.get("name") != name or row.get("expiry") != native_expiry
                    or row.get("instrumenttype") != option_instrument_type):
                continue
            try:
                row_strike = round(float(row["strike"]))
            except (KeyError, ValueError):
                continue
            if row_strike == target and row.get("symbol", "")[-2:] == option_type:
                return f"NFO|{row['token']}"
        raise InstrumentNotFound(
            f"no contract found: {symbol} {expiry_date} {strike}{option_type} "
            f"(expired, delisted, strike not listed, or the strike-divisor "
            f"auto-detection guessed wrong -- see this file's module docstring)"
        )

    def get_current_expiry(self, symbol: str) -> str:
        """
        Nearest unexpired option expiry for `symbol`, as ISO
        ("YYYY-MM-DD") -- see broker.py's Broker.get_current_expiry()
        docstring for why this must always be ISO regardless of broker.

        Angel One has no option-contract-list endpoint -- resolved via
        the same scrip master (OpenAPIScripMaster.json) this file
        already loads/caches. The master's own "expiry" field is
        native "DDMMMYYYY" (e.g. "17JUL2025"), parsed here and
        converted to ISO before returning, so callers never see
        Angel One's native format.
        """
        master = self._scrip_master()
        today = datetime.now(IST).date()
        expiries = set()
        option_instrument_type = _option_instrument_type(symbol)
        for row in master:
            if (row.get("name") != symbol or row.get("instrumenttype") != option_instrument_type
                    or row.get("exch_seg") != "NFO" or not row.get("expiry")):
                continue
            try:
                expiries.add(datetime.strptime(row["expiry"], "%d%b%Y").date())
            except ValueError:
                continue
        upcoming = sorted(e for e in expiries if e >= today)
        if not upcoming:
            raise InstrumentNotFound(f"no upcoming expiry found for {symbol}")
        return upcoming[0].isoformat()

    def resolve_equity_instrument_key(self, symbol: str) -> str:
        """
        Resolves `symbol` (e.g. "PNB") to its NSE cash-equity token,
        returned as "NSE|<token>" to match this file's instrument_key
        convention elsewhere (see _split_instrument_key()). Used for
        STOCK underlyings (config.STOCK_SYMBOLS) -- see upstox.py's
        method of the same name for the "why resolve dynamically
        instead of hardcoding" reasoning; same logic applies here.

        Matches on "name" == symbol first (Angel One's convention
        generally puts the plain symbol there for equities, unlike
        Upstox -- see the BUGFIX note in upstox.py's version of this
        method for why that field can't be trusted blindly across
        brokers). Falls back to "symbol" == "<SYMBOL>-EQ", Angel One's
        other common convention for the equity trading symbol field,
        in case a given scrip master snapshot doesn't populate "name"
        the expected way. STILL UNVERIFIED against a live download --
        confirm before depending on this live, the same way the
        Upstox equity match just needed a live-failure fix to get right.
        """
        master = self._scrip_master()
        eq_rows = [
            row for row in master
            if row.get("instrumenttype") == "EQ" and row.get("exch_seg") == "NSE"
        ]
        for row in eq_rows:
            if row.get("name") == symbol:
                token = row.get("token")
                if token:
                    return f"NSE|{token}"
        for row in eq_rows:
            if row.get("symbol") == f"{symbol}-EQ":
                token = row.get("token")
                if token:
                    return f"NSE|{token}"
        raise InstrumentNotFound(
            f"no NSE equity token found for '{symbol}' in the Angel One scrip "
            f"master (checked 'name' == '{symbol}' and 'symbol' == "
            f"'{symbol}-EQ' on instrumenttype=='EQ' rows) -- confirm the "
            f"symbol spelling and that an EQ row exists for it at all"
        )

    def resolve_current_month_future(self, symbol: str) -> str:
        """Same role as upstox.py's method of the same name -- see
        broker.py's docstring on why this isn't part of the required
        interface. Angel One scrip master instrumenttype for index
        futures is "FUTIDX", for stock futures "FUTSTK" (see
        _future_instrument_type())."""
        master = self._scrip_master()
        today = datetime.now(IST).date()
        candidates = []
        future_instrument_type = _future_instrument_type(symbol)
        for row in master:
            if row.get("name") != symbol or row.get("instrumenttype") != future_instrument_type:
                continue
            expiry_str = row.get("expiry")
            if not expiry_str:
                continue
            try:
                expiry_date = datetime.strptime(expiry_str, "%d%b%Y").date()
            except ValueError:
                continue
            if expiry_date >= today:
                candidates.append((expiry_date, row["token"]))
        if not candidates:
            raise InstrumentNotFound(
                f"no unexpired futures contract found for {symbol} in the Angel One "
                f"scrip master -- master may be stale, or field assumptions in this "
                f"function are wrong for your account segment"
            )
        candidates.sort(key=lambda pair: pair[0])
        return f"NFO|{candidates[0][1]}"


def _safe_float(value):
    """optionGreek's response has been observed with numeric fields as
    both quoted strings ("0.492400") and plain floats (0.6268) across
    different SDK/forum examples -- normalize either to float, or None
    if missing/unparseable rather than raising and losing every other
    strike's greeks over one bad field."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _split_instrument_key(instrument_key: str) -> tuple:
    if "|" not in instrument_key:
        raise BrokerAPIError(
            f"malformed Angel One instrument_key (expected 'EXCHANGE|TOKEN'): {instrument_key}"
        )
    exchange, token = instrument_key.split("|", 1)
    return exchange, token


def _iso_to_native_expiry(expiry_date_iso: str) -> str:
    """Converts an ISO expiry ('YYYY-MM-DD', what every caller of this
    Broker passes per broker.py's interface contract) to the Angel One
    scrip master's native format ('DDMMMYYYY', e.g. '17JUL2025') used
    for matching row["expiry"] in search_instrument()/get_option_chain()
    and the optionGreek endpoint's "expirydate" body param."""
    return datetime.strptime(expiry_date_iso, "%Y-%m-%d").strftime("%d%b%Y").upper()


def _option_instrument_type(name: str) -> str:
    """
    Angel One's scrip master uses DIFFERENT instrumenttype codes for
    index options vs. stock options -- "OPTIDX" for NIFTY/BANKNIFTY/
    MIDCPNIFTY, "OPTSTK" for individual stocks (config.STOCK_SYMBOLS).
    Every scrip-master filter in this file that used to hardcode
    "OPTIDX" now goes through this so stock symbols actually match
    rows in the master instead of silently finding zero and raising
    InstrumentNotFound on every call.
    """
    return "OPTSTK" if name in STOCK_SYMBOLS else "OPTIDX"


def _future_instrument_type(name: str) -> str:
    """Same split as _option_instrument_type() above, for futures:
    "FUTIDX" for indices, "FUTSTK" for individual stocks."""
    return "FUTSTK" if name in STOCK_SYMBOLS else "FUTIDX"


def _underlying_name(underlying_key: str) -> str:
    """underlying_key may be a plain name ('NIFTY') or an
    'EXCHANGE|TOKEN' key -- option chain lookups only need the name,
    which scanner.py's UNDERLYING_INSTRUMENT_KEYS TODO should be
    filled in with directly (e.g. just "NIFTY"), not a token, since
    unlike candles/LTP the option chain is looked up by name+expiry
    in the scrip master, not by a specific instrument."""
    return underlying_key.split("|")[-1] if "|" in underlying_key else underlying_key


def _detect_strike_divisor(master: list, name: str, expiry_date: str, instrument_type: str = "OPTIDX") -> float:
    """
    Auto-detects whether this scrip master snapshot stores strike
    prices as plain floats ("24500.000000") or multiplied by 100
    ("2450000.000000") -- see this file's module docstring, point 1.

    BUGFIX: the original heuristic compared divisibility-by-step
    (raw value % 50/100 == 0) against divisibility-by-step-after-
    dividing-by-100. That can never distinguish the two conventions:
    multiplying an already-round strike by 100 PRESERVES its
    divisibility by 50/100 (100 is itself a multiple of both), so a
    scaled master ties on both counts (e.g. 2,450,000 % 50 == 0 AND
    24,500 % 50 == 0) and the old `scaled_clean > raw_clean` check
    always fell through to 1.0 -- silently treating a scaled master
    as unscaled, which made every strike lookup mismatch.

    Fixed heuristic: magnitude, not divisibility. NIFTY/BANKNIFTY
    spot (and therefore ATM strikes) sit in the tens of thousands as
    of this writing; the x100 convention pushes that into the
    millions -- a gap divisibility can't distinguish but order of
    magnitude can. 100,000 is comfortably above any real strike and
    comfortably below any x100-scaled one.

    CAVEAT ADDED FOR STOCKS (2026-07-17, UNVERIFIED): this 100,000
    cutoff was calibrated for index-scale strikes. A lower-priced
    stock's strike could sit well under 100,000 even AFTER being
    scaled by 100 (e.g. a strike of 100 -> 10,000 scaled), which this
    heuristic would then misread as "unscaled" and silently return
    the wrong divisor. Confirm this against a real downloaded scrip
    master for each STOCK_SYMBOLS entry before trusting it live --
    don't assume the index-tuned cutoff transfers.
    """
    sample_strikes = [
        float(row["strike"]) for row in master
        if row.get("name") == name and row.get("expiry") == expiry_date
        and row.get("instrumenttype") == instrument_type and row.get("strike")
    ][:20]
    if not sample_strikes:
        return 1.0  # nothing to detect against; caller will raise InstrumentNotFound anyway
    avg_strike = sum(sample_strikes) / len(sample_strikes)
    return 100.0 if avg_strike > 100000 else 1.0
