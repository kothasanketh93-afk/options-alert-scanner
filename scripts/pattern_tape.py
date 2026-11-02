"""
Pattern tape (2026-11-02) -- a queryable, per-bar record of WHAT the chart looked like.
--------------------------------------------------------------------------------------
Every Trader run, for each configured index, one row per newly COMPLETED 5-minute candle:
  * the candle (OHLCV), ATR(14), 15-minute trend
  * every candlestick pattern detected on that candle (candlestick_patterns.detect)
  * every Signal-Lab strategy that fires ON this candle (signal_lab._build) and the list of strategies
    already fired today -- so any trade can later be explained: "pattern X + strategy Y at time T".
Observation only: never creates trades, never sends Telegram. One CSV per day on the state branch:
ledger/pattern_tape/YYYY-MM-DD.csv. Query with research/query_tape.py; rules live in strategy_catalog.py.
"""
import csv
import io
from pathlib import Path

import candlestick_patterns
import signal_lab

COLUMNS = ["date", "symbol", "bar_time", "open", "high", "low", "close", "volume", "atr14", "trend15",
           "gap_dir", "patterns", "pattern_net_bias", "signals_new", "signals_today", "prev_high", "prev_low",
           "prev_close"]


def complete_bars(today, now_minutes):
    """Only candles whose 5-minute window has closed (start + 5 <= now)."""
    return [c for c in today if signal_lab._mins(c) + 5 <= now_minutes]


def tape_row(symbol, warm, today, prev, gap_dir=None, narrow_cpr=False):
    """One row for the LAST candle of `today` (assumed complete), or None if there is not enough data."""
    if len(today) < 4:
        return None
    candles = list(warm) + list(today)
    off = len(warm)
    last = candles[-1]
    if gap_dir is None and prev:
        g = today[0]["open"] - prev[2]
        gap_dir = 1 if g > 0 else (-1 if g < 0 else 0)
    pv = (tuple(prev) + (narrow_cpr,)) if prev else None
    # _build only emits signals up to the second-to-last candle (it needs an entry candle), so append a
    # sentinel copy of the last candle: the real last candle becomes the last *evaluable* signal bar.
    sig, _ = signal_lab._build(candles + [dict(last)], off, pv, gap_dir)
    last_i = len(candles) - 1
    new = [f"{n}:{'CE' if dr == 1 else 'PE'}" for n, (i, dr) in sorted(sig.items()) if i == last_i]
    today_list = [f"{n}@{str(candles[i]['timestamp'])[11:16]}:{'CE' if dr == 1 else 'PE'}"
                  for n, (i, dr) in sorted(sig.items(), key=lambda kv: kv[1][0]) if i <= last_i]
    pats = candlestick_patterns.detect(candles[-30:])
    bias = sum(1 if p["bias"] == "bullish" else -1 if p["bias"] == "bearish" else 0 for p in pats)
    atr = signal_lab._atr(candles)[-1]
    trend = signal_lab._trend15(candles)[-1]
    return {
        "date": signal_lab._date(last), "symbol": symbol, "bar_time": str(last["timestamp"])[11:16],
        "open": last["open"], "high": last["high"], "low": last["low"], "close": last["close"],
        "volume": last.get("volume"), "atr14": round(atr, 2), "trend15": trend, "gap_dir": gap_dir,
        "patterns": ";".join(f"{p['pattern']}({p['bias'][0]})" for p in pats), "pattern_net_bias": bias,
        "signals_new": ";".join(new), "signals_today": ";".join(today_list),
        "prev_high": prev[0] if prev else None, "prev_low": prev[1] if prev else None,
        "prev_close": prev[2] if prev else None,
    }


def append_rows(rows, path):
    if not rows:
        return 0
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists() or path.stat().st_size == 0
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLUMNS, lineterminator="\n")
    if new:
        w.writeheader()
    w.writerows(rows)
    with open(path, "a", newline="") as f:
        f.write(buf.getvalue())
    return len(rows)
