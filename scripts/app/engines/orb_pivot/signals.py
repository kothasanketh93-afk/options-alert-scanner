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
