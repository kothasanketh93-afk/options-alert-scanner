"""
Pure signal functions (no I/O) for the liquidity-sweep shadow engine (added 2026-11-04, SHADOW ONLY).

All rules are FIXED before any data is seen -- do not tune them on results; a change = a new strategy name.
Candles: dicts (timestamp ISO IST, open, high, low, close), ascending, TODAY only, 5-minute.

Sweep (bullish / CE; bearish is the exact mirror, implemented by negating prices):
  * Levels below price: PDL (previous-day low) and OR_LOW (first-15-minute low, usable once the range is complete).
  * Price must have been on the inside before: the previous candle closed >= level (first candle: open >= level).
  * Sweep candle s: low < level. Reclaim candle r = s if close[s] > level, else s+1 if close[s+1] > level.
    No reclaim by s+1 = failed sweep (that level is dead for the day).
  * Reclaim candle must close bullish (close > open). extreme = lowest low of s..r.
  * Only the FIRST sweep event of the day per symbol is used; it must be fresh (signal on one of the last FRESH_WINDOW candles).

Strategies:
  sweep      - enter at the close of the reclaim candle.
  sweep_fvg  - after the reclaim, a displacement candle k (bullish, body >= 50% of range) leaves a fair value gap
               high[k-1] < low[k+1] (k from r-1 to r+2); enter on the first retest candle m (k+1 < m <= k+1+FVG_RETEST_WINDOW); the first displacement candle whose gap is retested wins
               that touches the gap (low <= low[k+1]), holds it (close > high[k-1]) and closes bullish. A close below the gap
               before the retest cancels the setup.
"""
from __future__ import annotations

OR_CANDLES = 3
FRESH_WINDOW = 2
LAST_SIGNAL_MINS = 14 * 60 + 5
FVG_RETEST_WINDOW = 6
DISPLACEMENT_BODY_FRAC = 0.5


def _mins(c: dict) -> int:
    ts = str(c["timestamp"])
    return int(ts[11:13]) * 60 + int(ts[14:16])


def _mirror(candles: list) -> list:
    return [{"timestamp": c["timestamp"], "open": -c["open"], "high": -c["low"],
             "low": -c["high"], "close": -c["close"]} for c in candles]


def _sweep_event(candles: list, level: float, start: int):
    """First bullish sweep of `level` at/after `start`. Returns dict or None."""
    n = len(candles)
    for s in range(start, n):
        c = candles[s]
        inside = (c["open"] >= level) if s == 0 else (candles[s - 1]["close"] >= level)
        if not inside or not c["low"] < level:
            continue
        if c["close"] > level:
            r = s
        elif s + 1 < n and candles[s + 1]["close"] > level:
            r = s + 1
        else:
            return None                      # failed sweep: level is dead
        rc = candles[r]
        if not rc["close"] > rc["open"]:
            return None
        return {"s": s, "r": r, "extreme": min(candles[j]["low"] for j in range(s, r + 1))}
    return None


def _bull_candidates(candles: list, levels: dict):
    """levels: {name: (value, start_index)}. Earliest sweep event across levels, or None."""
    best = None
    for name, (value, start) in levels.items():
        ev = _sweep_event(candles, value, start)
        if ev and (best is None or ev["r"] < best["r"]):
            best = dict(ev, level_name=name, level_value=value)
    return best


def _find_fvg(candles: list, ev: dict):
    n = len(candles)
    r = ev["r"]
    for k in range(max(r - 1, 1), r + 3):
        if k + 1 >= n:
            break
        a, b, c = candles[k - 1], candles[k], candles[k + 1]
        rng = max(b["high"] - b["low"], 1e-9)
        if not (b["close"] > b["open"] and (b["close"] - b["open"]) >= DISPLACEMENT_BODY_FRAC * rng):
            continue
        if not a["high"] < c["low"]:
            continue
        gap_lo, gap_hi = a["high"], c["low"]
        for m in range(k + 2, min(n, k + 2 + FVG_RETEST_WINDOW)):
            cm = candles[m]
            if cm["close"] < gap_lo:
                break                        # this gap was filled: cancelled, try the next displacement candle
            if cm["low"] <= gap_hi and cm["close"] > gap_lo and cm["close"] > cm["open"]:
                return {"m": m, "gap_lo": gap_lo, "gap_hi": gap_hi}
    return None


def _levels(candles, prev_high, prev_low, bullish: bool) -> dict:
    lv = {}
    if bullish:
        if prev_low is not None:
            lv["PDL"] = (prev_low, 0)
        if len(candles) > OR_CANDLES:
            lv["OR_LOW"] = (min(c["low"] for c in candles[:OR_CANDLES]), OR_CANDLES)
    else:
        if prev_high is not None:
            lv["PDH"] = (prev_high, 0)
        if len(candles) > OR_CANDLES:
            lv["OR_HIGH"] = (max(c["high"] for c in candles[:OR_CANDLES]), OR_CANDLES)
    return lv


def _detect(candles: list, prev_high, prev_low, with_fvg: bool):
    n = len(candles)
    if n < 3:
        return None
    found = []
    for direction, bullish in (("CE", True), ("PE", False)):
        cs = candles if bullish else _mirror(candles)
        lv = _levels(candles, prev_high, prev_low, bullish)
        if not bullish:   # mirrored levels: high-levels become low-levels of the mirrored series
            lv = {k: (-v, st) for k, (v, st) in lv.items()}
        ev = _bull_candidates(cs, lv)
        if not ev:
            continue
        idx = ev["r"]
        fvg = None
        if with_fvg:
            fvg = _find_fvg(cs, ev)
            if not fvg:
                continue
            idx = fvg["m"]
        if _mins(candles[idx]) > LAST_SIGNAL_MINS:
            continue
        sign = 1 if bullish else -1
        out = {"direction": direction, "level_name": ev["level_name"],
               "level_value": round(sign * ev["level_value"], 2), "signal_index": idx,
               "extreme": round(sign * ev["extreme"], 2), "signal_close": candles[idx]["close"]}
        if fvg:
            g1, g2 = sign * fvg["gap_lo"], sign * fvg["gap_hi"]
            out["fvg"] = (round(min(g1, g2), 2), round(max(g1, g2), 2))
        found.append((idx, out))
    if not found:
        return None
    idx, sig = min(found, key=lambda t: t[0])    # first event of the day
    return sig if idx >= n - FRESH_WINDOW else None


def detect_sweep(candles, prev_high, prev_low):
    return _detect(candles, prev_high, prev_low, with_fvg=False)


def detect_sweep_fvg(candles, prev_high, prev_low):
    return _detect(candles, prev_high, prev_low, with_fvg=True)


SL_MIN_ATR_FRAC = 0.5
T1_R, T2_R = 1.5, 2.5


def option_levels(premium: float, delta: float, signal_close: float, extreme: float, atr: float, direction: str) -> dict:
    """Structure stop: underlying risk = distance to the sweep extreme (floor 0.5 ATR), scaled by |delta|."""
    risk_u = max(abs(signal_close - extreme), SL_MIN_ATR_FRAC * atr)
    d = abs(delta) * risk_u
    sl = max(premium - d, premium * 0.5)
    return {"stop_loss": round(sl, 2), "target_1": round(premium + d * T1_R, 2),
            "target_2": round(premium + d * T2_R, 2), "risk_underlying": round(risk_u, 2)}
