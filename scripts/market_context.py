"""
market_context.py
-----------------
Regime context stamped onto every trade at the run that first sees it (runs are ~5 min apart, so the
stamp is the market state at alert time to within one cycle):

    vix_at_alert            India VIX level
    nifty_chg_pct_at_alert  NIFTY % change vs previous close

Capture only: nothing here gates or scores a trade. It exists so the weekly validation can test, with
evidence, whether results depend on the volatility / index-trend regime (the first week's results were
dominated by day direction). Every failure is swallowed -- context must never block a run.
"""
from logging_setup import get_logger

log = get_logger("market_context")
NIFTY_KEY = "NSE_INDEX|Nifty 50"
TRADE_LISTS = ("trades", "shadow_trades", "fib_pivot_shadow_trades", "orb_pivot_shadow_trades",
               "sweep_shadow_trades")


def nifty_change_pct(market_data, date_str):
    """NIFTY % change vs the last daily close before `date_str`, or None."""
    try:
        ltp = market_data._broker.get_ltp(NIFTY_KEY)
        prev = [c for c in market_data.get_recent_daily_candles(NIFTY_KEY, 5)
                if str(c.get("timestamp", ""))[:10] < date_str]
        if not ltp or not prev:
            return None
        pc = prev[-1]["close"]
        return round((ltp - pc) / pc * 100, 2) if pc else None
    except Exception as exc:
        log.warning("NIFTY change unavailable (%s: %s)", type(exc).__name__, exc)
        return None


def stamp_trades(state: dict, vix, nifty_chg) -> int:
    """Set the context fields on trades that do not carry them yet. Returns trades stamped."""
    n = 0
    for key in TRADE_LISTS:
        for trade in (state.get(key) or {}).values():
            if not isinstance(trade, dict) or "vix_at_alert" in trade:
                continue
            trade["vix_at_alert"] = vix
            trade["nifty_chg_pct_at_alert"] = nifty_chg
            n += 1
    return n


def day_type(o, h, l, c, prev_close):
    """Plain classification of one NIFTY session from its daily candle (capture only; nothing trades on it).

    Returns (label, details): label in trend_up / trend_down / range / mixed, with a gap_up / gap_down prefix when the
    open was >= 0.5% from the previous close and a volatile prefix when the high-low range was >= 1.5% of the open.
    body_ratio = |close-open| / (high-low): a trend day closes near one end of its range, a range day does not.
    """
    rng = h - l
    if not o or rng <= 0:
        return "unknown", {}
    body = abs(c - o) / rng
    base = ("trend_up" if c > o else "trend_down") if body >= 0.6 else ("range" if body < 0.35 else "mixed")
    tags = []
    gap = (o - prev_close) / prev_close * 100 if prev_close else 0.0
    if abs(gap) >= 0.5:
        tags.append("gap_up" if gap > 0 else "gap_down")
    if rng / o * 100 >= 1.5:
        tags.append("volatile")
    return "+".join(tags + [base]), {"gap_pct": round(gap, 2), "range_pct": round(rng / o * 100, 2), "body_ratio": round(body, 2)}


def record_day_type(market_data, date_str, now, path):
    """After the NSE close append today's NIFTY day type to ledger/day_types.csv, once. Best-effort."""
    import csv
    from pathlib import Path
    if (now.hour, now.minute) < (15, 35):
        return False
    path = Path(path)
    if path.exists() and any(r.get("date") == date_str for r in csv.DictReader(open(path, newline=""))):
        return False
    try:
        daily = market_data.get_recent_daily_candles(NIFTY_KEY, 5)
        today = [c for c in daily if str(c.get("timestamp", ""))[:10] == date_str]
        prev = [c for c in daily if str(c.get("timestamp", ""))[:10] < date_str]
        if not today or not prev:
            return False
        c0 = today[-1]
        label, det = day_type(c0["open"], c0["high"], c0["low"], c0["close"], prev[-1]["close"])
        new = not path.exists()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["date", "day_type", "open", "high", "low", "close", "prev_close", "gap_pct", "range_pct", "body_ratio"])
            w.writerow([date_str, label, c0["open"], c0["high"], c0["low"], c0["close"], prev[-1]["close"],
                        det.get("gap_pct"), det.get("range_pct"), det.get("body_ratio")])
        return True
    except Exception as exc:
        log.warning("Day type unavailable (%s: %s)", type(exc).__name__, exc)
        return False
