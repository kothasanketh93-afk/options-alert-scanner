"""
Commodity-specific shadow signals for MCX (added 2026-11-06). Pure functions, no I/O. SHADOW ONLY, fixed rules: a change = a new strategy name.

Why these exist: commodity options move mostly in the US session (IST 19:00-23:30 in winter / after US DST ends); the NSE-style morning
rules (09:00 opening range, 09:00-09:30 AMD) describe the quietest part of the MCX day. Times below are IST wall-clock.

  sess_orb    Session opening-range breakout. Range = 19:00-19:30 (six 5m candles; includes the 19:00 US data spike on NFP /
              CPI days). Range width must be 0.5-3.0 x ATR. First 5m close beyond the range, between 19:30 and 22:00, with a
              bullish/bearish body >= 50% of the candle range, is the signal. Stop reference = range midpoint. If the first
              close beyond the range fails the body test the day is skipped.
  sess_sweep  Day-session sweep. Levels = high/low of the 09:00-17:00 session. From 19:30, a wick through a level that is reclaimed
              (same or next candle, reclaim candle closing in the trade direction) is the signal. Stop reference = sweep extreme.
  ema_pb      EMA pullback with the trend. Window 17:00-22:30. Trend: EMA20 > EMA50 and EMA20 rising over 3 candles (mirror for
              shorts). Pullback: within the three candles before the trigger one low touched EMA20. Trigger: last completed
              candle closes above EMA20 and above the previous candle's high, bullish. Stop reference = lowest low of the last
              four candles.

News blackout (risk rule for the three strategies above): US-data minutes where 5m structure is unreliable (see in_news_blackout).
Candles: dicts (timestamp ISO IST, open, high, low, close), ascending, TODAY only, 5-minute.
"""
from __future__ import annotations

from app.engines.sweep_reversal import signals as base

SESSION_START = 19 * 60              # US data / pit-open window
OR_END = 19 * 60 + 30
ORB_LAST = 22 * 60
SWEEP_FIRST = 19 * 60 + 30
DAY_SESSION = (9 * 60, 17 * 60)
EMA_WINDOW = (17 * 60, 22 * 60 + 30)
ORB_MIN_ATR, ORB_MAX_ATR = 0.5, 3.0
BODY_FRAC = 0.5
FRESH = 2


def _mins(c) -> int:
    return base._mins(c)


def ema(values: list, period: int) -> list:
    k = 2.0 / (period + 1)
    out, prev = [], None
    for v in values:
        prev = v if prev is None else v * k + prev * (1 - k)
        out.append(prev)
    return out


def in_news_blackout(symbol: str, weekday: int, day_of_month: int, now_mins: int) -> bool:
    """True if a US data release makes new entries unreliable. weekday: Mon=0. Winter (EST) clock: 8:30 ET = 19:00 IST, 10:30 ET = 21:00 IST."""
    if weekday == 4 and day_of_month <= 7 and 18 * 60 + 55 <= now_mins <= 19 * 60 + 35:      # first-Friday payrolls (all)
        return True
    if symbol == "CRUDEOIL" and weekday == 2 and 20 * 60 + 55 <= now_mins <= 21 * 60 + 35:   # EIA crude inventories
        return True
    if symbol == "NATURALGAS" and weekday == 3 and 20 * 60 + 55 <= now_mins <= 21 * 60 + 35:  # EIA gas storage
        return True
    return False


def detect_session_orb(candles: list, atr: float):
    n = len(candles)
    if n < 8 or not atr or atr <= 0:
        return None
    rng_c = [c for c in candles if SESSION_START <= _mins(c) < OR_END]
    if len(rng_c) < 6:
        return None
    hi, lo = max(c["high"] for c in rng_c), min(c["low"] for c in rng_c)
    width = hi - lo
    if not ORB_MIN_ATR * atr <= width <= ORB_MAX_ATR * atr:
        return None
    for i, c in enumerate(candles):
        m = _mins(c)
        if m < OR_END or m > ORB_LAST:
            continue
        up, down = c["close"] > hi, c["close"] < lo
        if not (up or down):
            continue
        rng = max(c["high"] - c["low"], 1e-9)
        body = abs(c["close"] - c["open"])
        ok = body >= BODY_FRAC * rng and ((up and c["close"] > c["open"]) or (down and c["close"] < c["open"]))
        if not ok or i < n - FRESH:
            return None                      # first breakout failed the body test, or it is stale
        mid = round((hi + lo) / 2, 2)
        return {"direction": "CE" if up else "PE", "level_name": "SESS_ORB_HIGH" if up else "SESS_ORB_LOW",
                "level_value": round(hi if up else lo, 2), "signal_index": i, "extreme": mid, "signal_close": c["close"]}
    return None


def detect_session_sweep(candles: list):
    day = [c for c in candles if DAY_SESSION[0] <= _mins(c) < DAY_SESSION[1]]
    if len(day) < 20:
        return None
    start = next((i for i, c in enumerate(candles) if _mins(c) >= SWEEP_FIRST), None)
    if start is None:
        return None
    day_hi, day_lo = max(c["high"] for c in day), min(c["low"] for c in day)
    found = []
    for direction, bullish in (("CE", True), ("PE", False)):
        cs = candles if bullish else base._mirror(candles)
        level = day_lo if bullish else -day_hi
        ev = base._bull_candidates(cs, {"DAY_LOW" if bullish else "DAY_HIGH": (level, start)})
        if not ev or _mins(candles[ev["r"]]) > base.LAST_SIGNAL_MINS:
            continue
        sign = 1 if bullish else -1
        found.append((ev["r"], {"direction": direction, "level_name": ev["level_name"], "level_value": round(sign * ev["level_value"], 2),
                                "signal_index": ev["r"], "extreme": round(sign * ev["extreme"], 2), "signal_close": candles[ev["r"]]["close"]}))
    if not found:
        return None
    idx, sig = min(found, key=lambda t: t[0])
    return sig if idx >= len(candles) - FRESH else None


def detect_ema_pullback(candles: list):
    n = len(candles)
    if n < 60:
        return None
    last = candles[-1]
    if not EMA_WINDOW[0] <= _mins(last) <= EMA_WINDOW[1]:
        return None
    for direction, bullish in (("CE", True), ("PE", False)):
        cs = candles if bullish else base._mirror(candles)
        closes = [c["close"] for c in cs]
        e20, e50 = ema(closes, 20), ema(closes, 50)
        trig, prev = cs[-1], cs[-2]
        if not (e20[-1] > e50[-1] and e20[-1] > e20[-4]):
            continue
        if not any(cs[-1 - j]["low"] <= e20[-1 - j] for j in (1, 2, 3)):
            continue
        if not (trig["close"] > trig["open"] and trig["close"] > e20[-1] and trig["close"] > prev["high"]):
            continue
        sign = 1 if bullish else -1
        extreme = sign * min(c["low"] for c in cs[-4:])
        return {"direction": direction, "level_name": "EMA20", "level_value": round(sign * e20[-1], 2), "signal_index": n - 1,
                "extreme": round(extreme, 2), "signal_close": last["close"]}
    return None
