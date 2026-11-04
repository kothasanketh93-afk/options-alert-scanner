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


# ---------------------------------------------------------------------------------------------------------
# AMD (Accumulation - Manipulation - Distribution), added 2026-11-04. Fixed rules, shadow only:
#   Accumulation  : first AMD_ACC_CANDLES 5m candles (09:15-09:45) form a COMPRESSED range:
#                   (hi - lo) <= AMD_COMPRESSION_FRAC x previous day's (high - low).
#   Manipulation  : first later candle (before AMD_LAST_MANIP_MINS) that breaks one side of the range and is reclaimed
#                   (close back inside the range on the same or next candle, closing in the reclaim direction).
#   Distribution  : within AMD_CONFIRM_WINDOW candles of the reclaim, a candle closes beyond the range MIDPOINT in the
#                   opposite direction of the manipulation (colour-confirming) -> entry at that close.
#   Stop = manipulation extreme; targets by option_levels (1.5R / 2.5R). First event of the day, fresh only.
# ---------------------------------------------------------------------------------------------------------
AMD_ACC_CANDLES = 6
AMD_COMPRESSION_FRAC = 0.35
AMD_LAST_MANIP_MINS = 11 * 60 + 30
AMD_CONFIRM_WINDOW = 6


def detect_amd(candles, prev_high, prev_low):
    n = len(candles)
    if n <= AMD_ACC_CANDLES or prev_high is None or prev_low is None or prev_high <= prev_low:
        return None
    acc = candles[:AMD_ACC_CANDLES]
    hi, lo = max(c["high"] for c in acc), min(c["low"] for c in acc)
    if (hi - lo) > AMD_COMPRESSION_FRAC * (prev_high - prev_low):
        return None
    mid = (hi + lo) / 2.0
    found = []
    for direction, bullish in (("CE", True), ("PE", False)):
        cs = candles if bullish else _mirror(candles)
        lvl = lo if bullish else -hi
        mid_s = mid if bullish else -mid
        ev = _sweep_event(cs, lvl, AMD_ACC_CANDLES)
        if not ev or _mins(candles[ev["s"]]) > AMD_LAST_MANIP_MINS:
            continue
        r = ev["r"]
        m = next((j for j in range(r, min(n, r + 1 + AMD_CONFIRM_WINDOW))
                  if cs[j]["close"] > mid_s and cs[j]["close"] > cs[j]["open"]), None)
        if m is None or _mins(candles[m]) > LAST_SIGNAL_MINS:
            continue
        sign = 1 if bullish else -1
        found.append((m, {"direction": direction, "level_name": "ACC_LOW" if bullish else "ACC_HIGH",
                          "level_value": round(lo if bullish else hi, 2), "signal_index": m,
                          "extreme": round(sign * ev["extreme"], 2), "signal_close": candles[m]["close"],
                          "acc_mid": round(mid, 2)}))
    if not found:
        return None
    idx, sig = min(found, key=lambda t: t[0])
    return sig if idx >= n - FRESH_WINDOW else None


# ---------------------------------------------------------------------------------------------------------
# Order-flow PROXY ("oi_flow"). Real order flow (tick aggressor side, footprint, delta, DOM) is NOT available from
# the broker feed here, so this uses what is: option open-interest change around ATM vs price change.
#   snapshots: oldest..newest list of {"t": minutes-since-midnight, "spot", "call_oi", "put_oi"} (ATM +/- 3 strikes).
#   Compare the newest to the newest snapshot that is >= FLOW_LOOKBACK_MIN older.
#   CE: spot rose >= FLOW_MOVE_ATR x ATR AND put OI added > call OI added AND the imbalance >= FLOW_IMBALANCE_FRAC of
#       total OI (put writing / short covering = support).   PE: exact mirror (call writing = resistance).
# ---------------------------------------------------------------------------------------------------------
FLOW_LOOKBACK_MIN = 15
FLOW_MOVE_ATR = 0.25
FLOW_IMBALANCE_FRAC = 0.005
FLOW_FIRST_MINS = 9 * 60 + 45
FLOW_LAST_MINS = 14 * 60 + 5


def detect_oi_flow(snapshots: list, atr: float):
    if not snapshots or len(snapshots) < 2 or not atr or atr <= 0:
        return None
    now = snapshots[-1]
    if not FLOW_FIRST_MINS <= now["t"] <= FLOW_LAST_MINS:
        return None
    base = next((s for s in reversed(snapshots[:-1]) if now["t"] - s["t"] >= FLOW_LOOKBACK_MIN), None)
    if base is None:
        return None
    d_spot = now["spot"] - base["spot"]
    d_call, d_put = now["call_oi"] - base["call_oi"], now["put_oi"] - base["put_oi"]
    total = max(base["call_oi"] + base["put_oi"], 1)
    if d_spot >= FLOW_MOVE_ATR * atr and d_put > max(d_call, 0) and (d_put - d_call) >= FLOW_IMBALANCE_FRAC * total:
        direction = "CE"
    elif d_spot <= -FLOW_MOVE_ATR * atr and d_call > max(d_put, 0) and (d_call - d_put) >= FLOW_IMBALANCE_FRAC * total:
        direction = "PE"
    else:
        return None
    sign = 1 if direction == "CE" else -1
    return {"direction": direction, "level_name": "OI_FLOW", "level_value": round(now["spot"], 2),
            "signal_index": -1, "extreme": round(now["spot"] - sign * atr, 2), "signal_close": now["spot"],
            "d_spot": round(d_spot, 2), "d_call_oi": d_call, "d_put_oi": d_put}
