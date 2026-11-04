"""
Microstructure log (2026-11-01) -- REAL option quotes for execution-cost research.
---------------------------------------------------------------------------------
Why: the NIFTY +15/-10 backtest had no historical bid/ask, so slippage was an assumption. This
module records the real top-of-book quote, Greeks, OI and volume of the ATM +/- N strikes
(both sides) of each configured index on every Trader run, plus which shadow trades are open
on each contract. After a few weeks this answers, from evidence:
  * what the round-trip spread really costs per strike / time of day / DTE / expiry day
  * how much of a 10-15 point move is eaten by spread, and when the spread is tolerable
  * how theta (per-minute decay) compares to the targets at each DTE
  * how realistic fills (buy at ask, sell at bid) change shadow-trade results

Pure observation: never creates trades, never sends Telegram, never changes any gate. One CSV
per trading day: ledger/microstructure/YYYY-MM-DD.csv on the state branch (small files).
"""
import csv
import io
from datetime import datetime
from pathlib import Path

COLUMNS = ["ts", "date", "minute", "symbol", "expiry", "dte", "spot", "strike", "atm_offset",
           "side", "instrument_key", "ltp", "bid", "ask", "bid_qty", "ask_qty", "mid",
           "spread_abs", "spread_pct", "volume", "oi", "prev_oi", "delta", "gamma", "theta",
           "vega", "iv", "intrinsic", "trade_ids"]
MIN_GAP_MINUTES = 4          # runs are ~5 min apart; skip accidental double runs


def _num(x):
    try:
        return None if x is None else float(x)
    except (TypeError, ValueError):
        return None


def _nearest_strike(strikes, spot):
    return min(strikes, key=lambda s: abs(s - spot)) if strikes else None


def build_rows(symbol, expiry, dte, chain, now, width=2, trade_map=None):
    """Rows for ATM +/- width strikes (both sides) from one flat option chain.
    trade_map: {instrument_key: [trade_id, ...]} for open shadow trades (always included)."""
    trade_map = trade_map or {}
    strikes = sorted({r["strike_price"] for r in chain if r.get("strike_price") is not None})
    if not strikes:
        return []
    spot = next((_num(r.get("underlying_spot_price")) for r in chain
                 if _num(r.get("underlying_spot_price"))), None)
    if not spot:
        return []
    atm = _nearest_strike(strikes, spot)
    step = min((b - a for a, b in zip(strikes, strikes[1:])), default=50) or 50
    stamp = now.strftime("%Y-%m-%d %H:%M:%S")
    rows = []
    for r in chain:
        k = r.get("strike_price")
        if k is None:
            continue
        off = round((k - atm) / step)
        for side, p in (("CE", "call_"), ("PE", "put_")):
            ikey = r.get(p + "instrument_key")
            if ikey is None:
                continue
            tracked = trade_map.get(ikey)
            if abs(off) > width and not tracked:
                continue
            bid, ask, ltp = _num(r.get(p + "bid")), _num(r.get(p + "ask")), _num(r.get(p + "ltp"))
            two_sided = bid is not None and ask is not None and bid > 0 and ask > 0 and ask >= bid
            mid = round((bid + ask) / 2, 2) if two_sided else None
            spread = round(ask - bid, 2) if two_sided else None
            intrinsic = round(max(0.0, (spot - k) if side == "CE" else (k - spot)), 2)
            rows.append({
                "ts": stamp, "date": stamp[:10], "minute": now.hour * 60 + now.minute,
                "symbol": symbol, "expiry": expiry, "dte": dte, "spot": round(spot, 2),
                "strike": k, "atm_offset": off, "side": side, "instrument_key": ikey,
                "ltp": ltp, "bid": bid, "ask": ask,
                "bid_qty": _num(r.get(p + "bid_qty")), "ask_qty": _num(r.get(p + "ask_qty")),
                "mid": mid, "spread_abs": spread,
                "spread_pct": round(spread / mid * 100, 2) if spread is not None and mid else None,
                "volume": _num(r.get(p + "volume")), "oi": _num(r.get(p + "oi")),
                "prev_oi": _num(r.get(p + "prev_oi")), "delta": _num(r.get(p + "delta")),
                "gamma": _num(r.get(p + "gamma")), "theta": _num(r.get(p + "theta")),
                "vega": _num(r.get(p + "vega")), "iv": _num(r.get(p + "iv")),
                "intrinsic": intrinsic, "trade_ids": ";".join(tracked) if tracked else "",
            })
    rows.sort(key=lambda x: (x["strike"], x["side"]))
    return rows


def open_trade_map(state):
    """{instrument_key: [trade_id,...]} for every non-terminal shadow trade in state."""
    terminal = {"T2_HIT", "SL_HIT", "NOT_ENTERED", "AMBIGUOUS", "EOD_CLOSED"}
    out = {}
    for key in ("shadow_trades", "fib_pivot_shadow_trades", "orb_pivot_shadow_trades", "sweep_shadow_trades"):
        for tid, t in (state.get(key) or {}).items():
            status = t.get("final_status") or t.get("status")
            if status in terminal or not t.get("instrument_key"):
                continue
            out.setdefault(t["instrument_key"], []).append(tid)
    return out


def append_rows(rows, path):
    """Append rows to a daily CSV (header written once). Returns number written."""
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


def due(state, now):
    """True at most once per MIN_GAP_MINUTES within the session window."""
    mins = now.hour * 60 + now.minute
    if not (9 * 60 + 18 <= mins <= 15 * 60 + 28):
        return False
    last = state.get("micro_last_minute")
    day = now.strftime("%Y-%m-%d")
    if last and last[0] == day and mins - last[1] < MIN_GAP_MINUTES:
        return False
    return True


def mark_done(state, now):
    state["micro_last_minute"] = [now.strftime("%Y-%m-%d"), now.hour * 60 + now.minute]
