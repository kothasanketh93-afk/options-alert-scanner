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


# ---------------------------------------------------------------------------------------------------------
# Standalone FVG ("fvg"), added 2026-11-04 so the gap can be judged on its own, separately from sweeps. Fixed rules:
#   Displacement candle k: body >= 50% of its range, range >= FVG_MIN_RANGE_ATR x ATR, closing in the trade direction.
#   Gap: bullish high[k-1] < low[k+1] (bearish: mirror). Retest: first candle m (k+1 < m <= k+1+FVG_RETEST_WINDOW) that touches
#   the gap (low <= low[k+1]), closes above the gap bottom and closes bullish. A close below the gap bottom cancels that gap.
#   Entry at the retest close; stop = low of the displacement candle (floor 0.5 ATR). First event of the day, fresh only.
# ---------------------------------------------------------------------------------------------------------
FVG_MIN_RANGE_ATR = 1.0
FVG_FIRST_CANDLE = 3          # no signals from the opening noise (09:15-09:30)


def detect_fvg(candles, atr):
    n = len(candles)
    if n < FVG_FIRST_CANDLE + 3 or not atr or atr <= 0:
        return None
    found = []
    for direction, bullish in (("CE", True), ("PE", False)):
        cs = candles if bullish else _mirror(candles)
        for k in range(FVG_FIRST_CANDLE, n - 2):
            a, b, c = cs[k - 1], cs[k], cs[k + 1]
            rng = max(b["high"] - b["low"], 1e-9)
            if not (b["close"] > b["open"] and (b["close"] - b["open"]) >= DISPLACEMENT_BODY_FRAC * rng
                    and rng >= FVG_MIN_RANGE_ATR * atr and a["high"] < c["low"]):
                continue
            gap_lo, gap_hi = a["high"], c["low"]
            m_hit = None
            for m in range(k + 2, min(n, k + 2 + FVG_RETEST_WINDOW)):
                cm = cs[m]
                if cm["close"] < gap_lo:
                    break
                if cm["low"] <= gap_hi and cm["close"] > gap_lo and cm["close"] > cm["open"]:
                    m_hit = m
                    break
            if m_hit is None or _mins(candles[m_hit]) > LAST_SIGNAL_MINS:
                continue
            sign = 1 if bullish else -1
            g1, g2 = sign * gap_lo, sign * gap_hi
            found.append((m_hit, {"direction": direction, "level_name": "FVG",
                                  "level_value": round(g1, 2), "signal_index": m_hit,
                                  "extreme": round(sign * b["low"], 2), "signal_close": candles[m_hit]["close"],
                                  "fvg": (round(min(g1, g2), 2), round(max(g1, g2), 2))}))
            break
    if not found:
        return None
    idx, sig = min(found, key=lambda t: t[0])
    return sig if idx >= n - FRESH_WINDOW else None


# ---------------------------------------------------------------------------------------------------------
# EMA pullback with the trend ("ema_pb") and gap-and-go ("gap_orb"), added 2026-11-06. Fixed rules, shadow only.
#   ema_pb : trend = EMA(fast) > EMA(slow) and EMA(fast) rising over 3 candles (mirror for PE); pullback = within the three candles
#            before the trigger one low touched EMA(fast); trigger = last completed candle bullish, closes above EMA(fast) and above
#            the previous candle's high. Stop reference = lowest low of the last four candles. Only inside `window` (minutes of day).
#            NSE index: EMA 9/21, 10:30-14:15, >= 24 candles. MCX: EMA 20/50, 17:00-22:30, >= 60 candles (mcx_signals.py).
#   gap_orb: gap = first open vs previous close, |gap| >= GAP_MIN_PCT. The gap must hold through the first OR_CANDLES candles (a gap-up
#            never trades back to the previous close; mirror for gap-down). Signal = first 5m close beyond the opening-range extreme in
#            the gap direction before GAP_LAST_MINS, bullish/bearish body >= 50% of range. Stop reference = opposite OR extreme.
# ---------------------------------------------------------------------------------------------------------
NSE_EMA_WINDOW = (10 * 60 + 30, 14 * 60 + 15)
GAP_MIN_PCT = 0.30
GAP_LAST_MINS = 11 * 60


def ema(values: list, period: int) -> list:
    k = 2.0 / (period + 1)
    out, prev = [], None
    for v in values:
        prev = v if prev is None else v * k + prev * (1 - k)
        out.append(prev)
    return out


def detect_ema_pullback(candles: list, window: tuple, fast: int = 9, slow: int = 21, min_n: int = 24):
    n = len(candles)
    if n < max(min_n, slow + 4):
        return None
    last = candles[-1]
    if not window[0] <= _mins(last) <= window[1]:
        return None
    for direction, bullish in (("CE", True), ("PE", False)):
        cs = candles if bullish else _mirror(candles)
        closes = [c["close"] for c in cs]
        ef, es = ema(closes, fast), ema(closes, slow)
        trig, prev = cs[-1], cs[-2]
        if not (ef[-1] > es[-1] and ef[-1] > ef[-4]):
            continue
        if not any(cs[-1 - j]["low"] <= ef[-1 - j] for j in (1, 2, 3)):
            continue
        if not (trig["close"] > trig["open"] and trig["close"] > ef[-1] and trig["close"] > prev["high"]):
            continue
        sign = 1 if bullish else -1
        return {"direction": direction, "level_name": f"EMA{fast}", "level_value": round(sign * ef[-1], 2), "signal_index": n - 1,
                "extreme": round(sign * min(c["low"] for c in cs[-4:]), 2), "signal_close": last["close"]}
    return None


def detect_gap_orb(candles: list, prev_close):
    n = len(candles)
    if n < OR_CANDLES + 2 or not prev_close:
        return None
    gap_pct = (candles[0]["open"] - prev_close) / prev_close * 100
    if abs(gap_pct) < GAP_MIN_PCT:
        return None
    up = gap_pct > 0
    orc = candles[:OR_CANDLES]
    or_hi, or_lo = max(c["high"] for c in orc), min(c["low"] for c in orc)
    if (up and or_lo <= prev_close) or (not up and or_hi >= prev_close):
        return None                                   # the gap was (partly) filled during the opening range
    for i in range(OR_CANDLES, n):
        c = candles[i]
        if _mins(c) > GAP_LAST_MINS:
            return None
        beyond = c["close"] > or_hi if up else c["close"] < or_lo
        if not beyond:
            continue
        rng = max(c["high"] - c["low"], 1e-9)
        ok = abs(c["close"] - c["open"]) >= DISPLACEMENT_BODY_FRAC * rng and ((c["close"] > c["open"]) if up else (c["close"] < c["open"]))
        if not ok or i < n - FRESH_WINDOW:
            return None
        return {"direction": "CE" if up else "PE", "level_name": "GAP_OR_HIGH" if up else "GAP_OR_LOW",
                "level_value": round(or_hi if up else or_lo, 2), "signal_index": i,
                "extreme": round(or_lo if up else or_hi, 2), "signal_close": c["close"], "gap_pct": round(gap_pct, 2)}
    return None


# ---------------------------------------------------------------------------------------------------------
# Smart-money structure strategies, added 2026-11-07. Fixed rules, shadow only, same on NSE index and MCX (5m candles).
#   Swing high/low : fractal with SWING_L candles each side; confirmed only once SWING_L later candles exist (no look-ahead).
#   BOS (bullish)  : continuation break of structure. Candle k closes above the most recent confirmed, still-unbroken swing high;
#                    the structure was already making higher lows (last confirmed swing low > the one before it); k is a displacement
#                    candle (bullish, body >= 50% of range, range >= BOS_MIN_RANGE_ATR x ATR). A break that goes against the prior
#                    structure (CHoCH) is NOT traded. Signal on k (fresh: one of the last FRESH_WINDOW candles).
#                    Stop reference = the higher low (last confirmed swing low before k).
#   Order block    : after a bullish BOS at k, the OB is the last bearish candle in the OB_LOOKBACK candles before k (its full
#                    high-low range). Signal on the FIRST return into the zone within OB_RETEST_WINDOW candles of k, when that candle
#                    touches the zone (low <= OB high), closes back above the OB high and closes bullish. A close below the OB low,
#                    or a touch without that reaction, kills the setup. Stop reference = OB low.
#   Bearish versions are exact mirrors. CE = bullish, PE = bearish.
# ---------------------------------------------------------------------------------------------------------
SWING_L = 2
BOS_MIN_RANGE_ATR = 0.8
OB_LOOKBACK = 5
OB_RETEST_WINDOW = 12
SMC_MIN_CANDLES = 14
NSE_SMC_WINDOW = (9 * 60 + 45, 14 * 60 + 15)


def _swing_points(cs: list, upto: int):
    """Confirmed swing highs/lows among candles[0..upto-1]: a swing at i needs i + SWING_L <= upto - 1."""
    highs, lows = [], []
    for i in range(SWING_L, upto - SWING_L):
        if all(cs[i]["high"] > cs[i - j]["high"] and cs[i]["high"] >= cs[i + j]["high"] for j in range(1, SWING_L + 1)):
            highs.append(i)
        if all(cs[i]["low"] < cs[i - j]["low"] and cs[i]["low"] <= cs[i + j]["low"] for j in range(1, SWING_L + 1)):
            lows.append(i)
    return highs, lows


def _bos_events(cs: list, atr: float) -> list:
    """Bullish continuation BOS events in `cs` (mirror the candles for bearish). [(k, broken_level, higher_low_price)]"""
    out = []
    for k in range(2 * SWING_L + 2, len(cs)):
        highs, lows = _swing_points(cs, k)            # only structure confirmed before candle k
        if not highs or len(lows) < 2:
            continue
        s = highs[-1]
        if any(cs[j]["close"] > cs[s]["high"] for j in range(s + 1, k)):
            continue                                  # that high was already broken: not a fresh break
        if cs[lows[-1]]["low"] <= cs[lows[-2]]["low"]:
            continue                                  # no higher low: a reversal (CHoCH), not a continuation
        c = cs[k]
        rng = max(c["high"] - c["low"], 1e-9)
        if (c["close"] > cs[s]["high"] and c["close"] > c["open"] and (c["close"] - c["open"]) >= DISPLACEMENT_BODY_FRAC * rng
                and rng >= BOS_MIN_RANGE_ATR * atr):
            out.append((k, cs[s]["high"], cs[lows[-1]]["low"]))
    return out


def detect_bos(candles: list, atr: float, window: tuple = NSE_SMC_WINDOW):
    n = len(candles)
    if n < SMC_MIN_CANDLES or not atr or atr <= 0 or not window[0] <= _mins(candles[-1]) <= window[1]:
        return None
    for direction, bullish in (("CE", True), ("PE", False)):
        cs = candles if bullish else _mirror(candles)
        ev = [e for e in _bos_events(cs, atr) if e[0] >= n - FRESH_WINDOW]
        if not ev:
            continue
        k, level, hl = ev[-1]
        sign = 1 if bullish else -1
        return {"direction": direction, "level_name": "BOS", "level_value": round(sign * level, 2), "signal_index": k,
                "extreme": round(sign * hl, 2), "signal_close": candles[k]["close"]}
    return None


def detect_order_block(candles: list, atr: float, window: tuple = NSE_SMC_WINDOW):
    n = len(candles)
    if n < SMC_MIN_CANDLES or not atr or atr <= 0 or not window[0] <= _mins(candles[-1]) <= window[1]:
        return None
    for direction, bullish in (("CE", True), ("PE", False)):
        cs = candles if bullish else _mirror(candles)
        for k, _level, _hl in reversed(_bos_events(cs, atr)):
            if k > n - 2 or n - 1 - k > OB_RETEST_WINDOW:
                continue
            ob = next((o for o in range(k - 1, max(-1, k - 1 - OB_LOOKBACK), -1) if cs[o]["close"] < cs[o]["open"]), None)
            if ob is None:
                continue
            lo, hi = cs[ob]["low"], cs[ob]["high"]
            for m in range(k + 1, n):
                c = cs[m]
                if c["close"] < lo:
                    break
                if c["low"] <= hi:                    # first return into the zone decides the setup
                    if c["close"] > hi and c["close"] > c["open"] and m >= n - FRESH_WINDOW:
                        sign = 1 if bullish else -1
                        a, b = sign * lo, sign * hi
                        return {"direction": direction, "level_name": "OB", "level_value": round(b, 2), "signal_index": m,
                                "extreme": round(a, 2), "signal_close": candles[m]["close"],
                                "ob": (round(min(a, b), 2), round(max(a, b), 2))}
                    break
    return None
