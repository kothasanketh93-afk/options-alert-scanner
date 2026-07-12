"""
market_data.py
---------------
Thin wrapper around Upstox's historical/intraday candle endpoint,
scoped to what tracker.py needs: 1-minute candles for one option
instrument, from a given timestamp to now.

NOTE: Verify the exact endpoint path/params against Upstox's current
API docs before relying on this in production — API surfaces do
change, and you already have a working Upstox integration in your
existing scanner that you should reuse/adapt here instead of trusting
this in isolation.
"""

import os
import time
import requests
from datetime import datetime, timezone, timedelta

from config import RETRY_MAX_ATTEMPTS, RETRY_BACKOFF_SECONDS, RETRYABLE_STATUS_CODES

IST = timezone(timedelta(hours=5, minutes=30))
BASE_URL = "https://api.upstox.com/v2"

ACCESS_TOKEN = os.environ.get("UPSTOX_ACCESS_TOKEN")

# Retry only transient failures (network blips, rate limits, momentary
# 5xx) -- NOT auth errors (401/403, e.g. expired token) or bad-request
# errors (4xx other than 429), which retrying can't fix and which should
# fail loudly and immediately instead of silently retrying and burning
# the Actions job's timeout. Thresholds live in config.py.


def _get_with_retry(url: str, timeout: int = 15):
    last_exc = None
    for attempt in range(RETRY_MAX_ATTEMPTS):
        try:
            resp = requests.get(url, headers=_headers(), timeout=timeout)
        except requests.RequestException as exc:
            last_exc = exc
        else:
            if resp.status_code not in RETRYABLE_STATUS_CODES:
                resp.raise_for_status()
                return resp
            last_exc = requests.HTTPError(
                f"{resp.status_code} from {url}", response=resp
            )
        if attempt < RETRY_MAX_ATTEMPTS - 1:
            time.sleep(RETRY_BACKOFF_SECONDS * (2 ** attempt))
    raise last_exc


def _headers():
    return {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Accept": "application/json",
    }


def get_1min_candles_since(instrument_key: str, since_iso: str) -> list:
    """
    Fetches 1-minute candles for `instrument_key` from `since_iso`
    (exclusive) up to now, sorted ascending by timestamp.

    Upstox intraday candle API returns candles for the current trading
    day only, newest-first, as [timestamp, open, high, low, close, volume, oi].
    We fetch the full available intraday set and filter client-side —
    simplest correct approach, and cheap given it's one call per open
    trade per run (small numbers by design here).
    """
    if not ACCESS_TOKEN:
        raise RuntimeError("UPSTOX_ACCESS_TOKEN not set")

    since_dt = datetime.fromisoformat(since_iso)
    if since_dt.tzinfo is None:
        since_dt = since_dt.replace(tzinfo=IST)

    url = f"{BASE_URL}/historical-candle/intraday/{instrument_key}/1minute"
    resp = _get_with_retry(url)
    payload = resp.json()

    raw_candles = payload.get("data", {}).get("candles", [])
    out = []
    for c in raw_candles:
        ts = datetime.fromisoformat(c[0])
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=IST)
        if ts <= since_dt:
            continue
        out.append({
            "timestamp": ts.isoformat(),
            "open": float(c[1]),
            "high": float(c[2]),
            "low": float(c[3]),
            "close": float(c[4]),
        })

    out.sort(key=lambda c: c["timestamp"])
    return out


def get_recent_underlying_candles(instrument_key: str, interval: str, count: int) -> list:
    """
    Fetches the most recent `count` candles for the UNDERLYING index
    (NIFTY/BANKNIFTY spot or futures instrument_key, not an option) at
    the given interval. Used by scanner.py for multi-timeframe EMA
    confirmation, regime detection, AND the mandatory VWAP filter --
    all pure computation once fetched, so this is the only extra API
    call those features need, once per symbol per run.

    interval: "5minute" or "15minute" (Upstox's historical-candle
    endpoint accepts a small fixed set of interval strings -- confirm
    exact accepted values against current Upstox docs).

    IMPORTANT: includes "volume" in each candle -- scanner.compute_vwap()
    requires it and fails closed (returns None, which vwap_confirms()
    then reads as "reject") without it. Upstox's candle array format is
    [timestamp, open, high, low, close, volume, oi] per the docstring
    on get_1min_candles_since() above -- index 5 is volume.

    Returns ascending-sorted OHLCV candles, most recent `count` only.
    """
    if not ACCESS_TOKEN:
        raise RuntimeError("UPSTOX_ACCESS_TOKEN not set")

    url = f"{BASE_URL}/historical-candle/intraday/{instrument_key}/{interval}"
    resp = _get_with_retry(url)
    payload = resp.json()

    raw_candles = payload.get("data", {}).get("candles", [])
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
