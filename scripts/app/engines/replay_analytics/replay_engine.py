"""
app/engines/replay_analytics/replay_engine.py -- Replay Engine.
------------------------------------------------------------------------------
Replays historical candles candle-by-candle, exactly the way tracker.py
consumes live 1-minute candles, across every timeframe in
config.REPLAY_TIMEFRAMES. Source data is ALWAYS 1-minute candles
(market_data.get_1min_candles_since() / get_recent_underlying_candles());
every other timeframe is produced by resampling those UP in this
module -- never fetched separately, so widening the replay to more
timeframes never multiplies broker API calls.

Candle dict shape matches tracker.py exactly:
    {"timestamp": "2026-07-13T09:43:00+05:30",
     "open": 120.1, "high": 122.4, "low": 119.8, "close": 121.9}

This module has NO knowledge of trades/signals -- it only turns raw
1-minute candles into the requested timeframe's candles. signal_replay.py
is what walks a trade's lifecycle against the resulting candle list.
"""

from datetime import datetime, timezone, timedelta

from config import REPLAY_TIMEFRAMES, REPLAY_TIMEFRAME_MINUTES, REPLAY_ENABLE_RESULT_CACHE
from logging_setup import get_logger

IST = timezone(timedelta(hours=5, minutes=30))
_logger = get_logger("replay_analytics")


def _parse_ts(ts) -> datetime:
    if isinstance(ts, datetime):
        dt = ts
    else:
        dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    return dt


def _bucket_start_1min_multiple(dt: datetime, minutes: int) -> datetime:
    """Floors dt to the start of its N-minute bucket, anchored to the
    top of the hour (matches NSE session convention: 9:15, 9:20, 9:25
    for a 5-min bucket, not an arbitrary offset)."""
    total_minutes = dt.hour * 60 + dt.minute
    bucket_minutes = (total_minutes // minutes) * minutes
    return dt.replace(hour=bucket_minutes // 60, minute=bucket_minutes % 60,
                       second=0, microsecond=0)


def resample_candles(one_min_candles: list, timeframe: str) -> list:
    """
    Resamples a chronologically-sorted list of 1-minute candles up to
    `timeframe` (one of config.REPLAY_TIMEFRAMES). Returns [] on empty
    input. "1min" (or any unrecognized value) returns the input
    unchanged -- this is the native tracker.py granularity, no
    resampling needed.
    """
    if not one_min_candles:
        return []
    if timeframe not in REPLAY_TIMEFRAME_MINUTES and timeframe != "1day":
        return list(one_min_candles)

    candles = sorted(one_min_candles, key=lambda c: _parse_ts(c["timestamp"]))

    if timeframe == "1day":
        key_fn = lambda dt: dt.date()
    else:
        minutes = REPLAY_TIMEFRAME_MINUTES[timeframe]
        key_fn = lambda dt: _bucket_start_1min_multiple(dt, minutes)

    buckets = {}
    order = []
    for c in candles:
        dt = _parse_ts(c["timestamp"])
        key = key_fn(dt)
        if key not in buckets:
            buckets[key] = {
                "timestamp": (dt.isoformat() if timeframe == "1day"
                              else key.isoformat() if hasattr(key, "isoformat") else str(key)),
                "open": c["open"], "high": c["high"], "low": c["low"], "close": c["close"],
            }
            order.append(key)
        else:
            b = buckets[key]
            b["high"] = max(b["high"], c["high"])
            b["low"] = min(b["low"], c["low"])
            b["close"] = c["close"]

    return [buckets[k] for k in order]


class ReplayEngine:
    """
    Drives a replay across every configured timeframe for one
    (symbol, direction) history of 1-minute candles. Stateless per
    call -- the optional in-process cache only avoids re-resampling
    the SAME 1-min candle list to the SAME timeframe twice within one
    analytics run (e.g. daily + weekly + monthly all replaying the
    same historical window).
    """

    def __init__(self):
        self._cache = {} if REPLAY_ENABLE_RESULT_CACHE else None

    def resample(self, one_min_candles: list, timeframe: str) -> list:
        if self._cache is None:
            return resample_candles(one_min_candles, timeframe)
        cache_key = (id(one_min_candles), len(one_min_candles), timeframe)
        if cache_key in self._cache:
            return self._cache[cache_key]
        result = resample_candles(one_min_candles, timeframe)
        self._cache[cache_key] = result
        return result

    def resample_all_timeframes(self, one_min_candles: list) -> dict:
        """Returns {timeframe: candles} for every config.REPLAY_TIMEFRAMES entry."""
        return {tf: self.resample(one_min_candles, tf) for tf in REPLAY_TIMEFRAMES}
