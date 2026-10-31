"""
Pure signal functions (no I/O) for the ORB15 and pivot-break shadow engines.
Candles are dicts with timestamp(ISO, IST), open, high, low, close, ordered
ascending, TODAY only. Direction: "CE" (up) or "PE" (down).
"""
from __future__ import annotations

OR_CANDLES = 3            # 3 x 5m = first 15 minutes
FRESH_WINDOW = 2          # signal candle must be one of the last N candles
LAST_SIGNAL_MINS = 15 * 60 + 5   # last signal candle start (tested range)


def _mins(candle: dict) -> int:
    ts = str(candle["timestamp"])
    hh, mm = int(ts[11:13]), int(ts[14:16])
    return hh * 60 + mm


def classic_pivots(prev_high: float, prev_low: float, prev_close: float) -> dict:
    p = (prev_high + prev_low + prev_close) / 3.0
    return {"P": p, "R1": 2 * p - prev_low, "S1": 2 * p - prev_high}


def _fresh(index: int, n: int) -> bool:
    return index >= n - FRESH_WINDOW


def detect_orb15(candles: list, prev_close: float):
    """
    First candle after the opening range whose close breaks the range in the
    direction of the gap. Returns dict or None.
    """
    n = len(candles)
    if n <= OR_CANDLES or prev_close is None:
        return None
    gap = candles[0]["open"] - prev_close
    if gap == 0:
        return None
    want = "CE" if gap > 0 else "PE"
    hi = max(c["high"] for c in candles[:OR_CANDLES])
    lo = min(c["low"] for c in candles[:OR_CANDLES])
    for i in range(OR_CANDLES, n):
        if _mins(candles[i]) > LAST_SIGNAL_MINS:
            break
        c = candles[i]["close"]
        direction = "CE" if c > hi else ("PE" if c < lo else None)
        if direction != want:
            continue
        if not _fresh(i, n):
            return None   # first qualifying break is stale; do not chase
        return {"direction": direction, "level_name": "OR_HIGH" if direction == "CE" else "OR_LOW",
                "level_value": round(hi if direction == "CE" else lo, 2),
                "signal_index": i, "gap": round(gap, 2)}
    return None


def detect_pivot_break(candles: list, prev_high: float, prev_low: float, prev_close: float):
    """First fresh 5m close through classic R1/S1 with confirming candle colour."""
    n = len(candles)
    if n < 4 or None in (prev_high, prev_low, prev_close):
        return None
    pv = classic_pivots(prev_high, prev_low, prev_close)
    for i in range(3, n):
        if _mins(candles[i]) > LAST_SIGNAL_MINS:
            break
        c, o, pc = candles[i]["close"], candles[i]["open"], candles[i - 1]["close"]
        direction = level = None
        if c > pv["R1"] and pc <= pv["R1"] and c > o:
            direction, level = "CE", "R1"
        elif c < pv["S1"] and pc >= pv["S1"] and c < o:
            direction, level = "PE", "S1"
        if direction is None:
            continue
        if not _fresh(i, n):
            return None
        return {"direction": direction, "level_name": level, "level_value": round(pv[level], 2),
                "signal_index": i, "pivots": {k: round(v, 2) for k, v in pv.items()}}
    return None


def option_levels(premium: float, delta: float, atr: float, target_atr_mult: float,
                  stop_atr_mult: float = 1.0) -> dict:
    """Premium-level stop/targets: underlying ATR distances scaled by |delta|."""
    d = abs(delta) * atr
    sl = max(premium - d * stop_atr_mult, premium * 0.5)
    t1 = premium + d * target_atr_mult
    t2 = t1 + d * 1.0
    return {"stop_loss": round(sl, 2), "target_1": round(t1, 2), "target_2": round(t2, 2)}


def is_wide_prev_day(daily: list, window: int = 10, factor: float = 1.5):
    """
    True if the prior day's high-low range exceeds `factor` x the mean range of the last
    `window` days (including the prior day) -- the backtest's "wide day" tag, where ORB
    did worst. Returns None when fewer than 5 days are available. Tag only; never a filter.
    """
    ranges = [d["high"] - d["low"] for d in daily[-window:] if d.get("high") is not None and d.get("low") is not None]
    if len(ranges) < 5:
        return None
    return ranges[-1] > factor * (sum(ranges) / len(ranges))


PDHL_LAST_SIGNAL_MINS = 14 * 60 + 5   # as in the backtest
PDHL_RETEST_WINDOW = 12               # candles after the breakout in which the retest must occur


def _ema_series(values: list, span: int) -> list:
    k = 2.0 / (span + 1)
    out, prev = [], None
    for v in values:
        prev = v if prev is None else v * k + prev * (1 - k)
        out.append(prev)
    return out


def detect_pdhl_retest(candles: list, prev_high: float, prev_low: float):
    """
    Previous-day High/Low breakout + retest (rules fixed in the 2026-10-31 backtest, no tuning):
      1. A 5m candle CLOSES beyond PDH (CE) / PDL (PE) with a body >= 50% of its range and in the
         breakout direction (wick-only breaks ignored).
      2. Within PDHL_RETEST_WINDOW candles a later candle touches the level (CE: low <= PDH; PE: high >= PDL)
         and CLOSES back on the breakout side with a confirming colour, while EMA9 > EMA21 (CE) / < (PE).
      3. Any close back through the level before the retest cancels the setup (failed breakout).
    Only a signal on one of the last FRESH_WINDOW candles is returned. EMAs use today's candles only.
    """
    n = len(candles)
    if n < 6 or None in (prev_high, prev_low):
        return None
    closes = [c["close"] for c in candles]
    e9, e21 = _ema_series(closes, 9), _ema_series(closes, 21)
    st = None
    for i in range(3, n):
        if _mins(candles[i]) > PDHL_LAST_SIGNAL_MINS:
            break
        c = candles[i]
        cl, o, h, l = c["close"], c["open"], c["high"], c["low"]
        body, rng = abs(cl - o), max(h - l, 1e-9)
        if st is None:
            if cl > prev_high and body >= 0.5 * rng and cl > o:
                st = ("CE", i)
            elif cl < prev_low and body >= 0.5 * rng and cl < o:
                st = ("PE", i)
            continue
        direction, bi = st
        if i - bi > PDHL_RETEST_WINDOW:
            return None
        if direction == "CE":
            if cl < prev_high:
                return None
            if l <= prev_high < cl and cl > o and e9[i] > e21[i]:
                return (None if not _fresh(i, n) else
                        {"direction": "CE", "level_name": "PDH", "level_value": round(prev_high, 2), "signal_index": i})
        else:
            if cl > prev_low:
                return None
            if h >= prev_low > cl and cl < o and e9[i] < e21[i]:
                return (None if not _fresh(i, n) else
                        {"direction": "PE", "level_name": "PDL", "level_value": round(prev_low, 2), "signal_index": i})
    return None
