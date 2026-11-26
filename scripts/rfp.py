"""
Regime-First Pullback (RFP) -- SHADOW ONLY strategy, designed 2026-11-26 from the Nov-2026 ledger (hypothesis, not proven).

Idea: classify each instrument's day first (trend up / trend down / chop); trade ONLY in the trend direction, only on a
pullback to the EMA with a bullish/bearish trigger candle, only inside the instrument's best time window, with a structure
stop and a break-even stop once the option is up `be_trigger_pct`. Chop days = no trade.

Pure functions, no I/O. Candles: dicts (timestamp ISO IST, open, high, low, close), ascending, 5-minute.
Rules are FIXED once the shadow run starts (a change = a new strategy name). Acceptance test (pre-registered): per instrument
>= 30 closed trades over >= 20 trading days, net average > +3%, day-block bootstrap 90% range > 0; otherwise the
instrument is dropped, not re-tuned.
"""
from __future__ import annotations

from app.engines.sweep_reversal import signals as base

OR_CANDLES = 6                       # opening range = first 6 five-minute candles of the session
DRIFT_ATR_FRAC = 0.25                # |close - first open| must exceed this x ATR to count as a drift vote
EMA_FAST, EMA_SLOW, MIN_N = 9, 21, 24
MIN_CANDLES_FOR_REGIME = OR_CANDLES + 2

M = lambda h, m=0: h * 60 + m        # noqa: E731

# window = (first, last) minute-of-day (IST) of the TRIGGER candle's timestamp.
# pe_min_score: PE needs at least this many bearish votes (3 = all three). Set from Nov-2026 data: NIFTY/SENSEX PE were
# 20-27% win rate (probably a one-sided month, so PE is allowed only on a fully confirmed down day rather than banned).
# be_trigger_pct: once the option's favourable move reaches this %, the stop moves to entry (tracker, per trade).
PARAMS = {
    "BANKNIFTY":  {"window": (M(9, 45), M(11, 30)), "pe_min_score": 2, "be_trigger_pct": 8.0},
    "NIFTY":      {"window": (M(9, 30), M(11, 30)), "pe_min_score": 3, "be_trigger_pct": 8.0},
    "SENSEX":     {"window": (M(9, 45), M(11, 30)), "pe_min_score": 3, "be_trigger_pct": 8.0},
    "MIDCPNIFTY": {"window": (M(9, 45), M(11, 30)), "pe_min_score": 2, "be_trigger_pct": 8.0},
    # MCX: evening / US-session overlap; natural gas morning was 12% win rate in Nov-2026 so its window starts at midday.
    "CRUDEOIL":   {"window": (M(17, 0), M(22, 30)), "pe_min_score": 2, "be_trigger_pct": 5.0},
    "NATURALGAS": {"window": (M(12, 0), M(22, 30)), "pe_min_score": 2, "be_trigger_pct": 5.0},
    "GOLDM":      {"window": (M(17, 0), M(22, 30)), "pe_min_score": 2, "be_trigger_pct": 5.0},
}
NSE_SYMBOLS = ("BANKNIFTY", "NIFTY", "SENSEX", "MIDCPNIFTY")
MCX_SYMBOLS = ("CRUDEOIL", "NATURALGAS", "GOLDM")


def _vwap_like(candles: list) -> float | None:
    """Cumulative volume-weighted typical price; falls back to the plain typical-price mean when volume is missing/zero."""
    num = den = 0.0
    tp_sum = 0.0
    for c in candles:
        tp = (c["high"] + c["low"] + c["close"]) / 3.0
        v = c.get("volume") or 0
        num += tp * v
        den += v
        tp_sum += tp
    if den > 0:
        return num / den
    return tp_sum / len(candles) if candles else None


def regime(today: list, atr: float) -> dict:
    """
    Three votes, each +1 (up) / -1 (down) / 0:
      vwap   last close above / below the day's VWAP
      orb    last close above the opening-range high / below its low
      drift  last close more than DRIFT_ATR_FRAC x ATR above / below the day's first open
    score >= 2 -> "UP", <= -2 -> "DOWN", else "CHOP". Returns {"regime", "score", "votes"}.
    """
    if len(today) < MIN_CANDLES_FOR_REGIME or not atr or atr <= 0:
        return {"regime": "NONE", "score": 0, "votes": {}}
    last = today[-1]["close"]
    vw = _vwap_like(today)
    orc = today[:OR_CANDLES]
    hi, lo = max(c["high"] for c in orc), min(c["low"] for c in orc)
    votes = {
        "vwap": 0 if vw is None else (1 if last > vw else -1 if last < vw else 0),
        "orb": 1 if last > hi else -1 if last < lo else 0,
        "drift": (1 if last - today[0]["open"] > DRIFT_ATR_FRAC * atr
                  else -1 if today[0]["open"] - last > DRIFT_ATR_FRAC * atr else 0),
    }
    score = sum(votes.values())
    return {"regime": "UP" if score >= 2 else "DOWN" if score <= -2 else "CHOP", "score": score, "votes": votes}


def detect(symbol: str, today: list, all_candles: list, atr: float):
    """
    RFP signal or None. `today`: today's 5m candles (regime inputs). `all_candles`: previous-session tail + today (EMA warm-up),
    same ascending series ending at the same last candle.
    """
    p = PARAMS.get(symbol)
    if not p or not today or not all_candles:
        return None
    rg = regime(today, atr)
    if rg["regime"] not in ("UP", "DOWN"):
        return None
    direction = "CE" if rg["regime"] == "UP" else "PE"
    if direction == "PE" and abs(rg["score"]) < p["pe_min_score"]:
        return None
    sig = base.detect_ema_pullback(all_candles, p["window"], EMA_FAST, EMA_SLOW, MIN_N)
    if not sig or sig["direction"] != direction:
        return None
    sig = dict(sig)
    sig.update({"regime": rg["regime"], "regime_score": rg["score"], "regime_votes": rg["votes"],
                "be_trigger_pct": p["be_trigger_pct"], "level_name": f"RFP·EMA{EMA_FAST}"})
    return sig
