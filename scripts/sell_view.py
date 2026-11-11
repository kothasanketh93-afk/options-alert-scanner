"""Buy vs sell comparison (added 2026-11-11). Data collection only: no alerts, no orders, never changes a trade's own outcome.

Every tracked setup is a BUY of an option in the signal's direction (bullish -> buy CE, bearish -> buy PE). An option seller
expresses the SAME view by SELLING the opposite option instead (bullish -> sell PE, bearish -> sell CE). For each entered
and closed trade this module records what that sale would have returned over the SAME holding window:

  sell at the opposite option's 1-minute close at the buy trade's entry minute,
  buy back at its 1-minute close at the buy trade's exit minute (SL / T2 / end-of-day close).

Same strike and expiry as the bought option. The seller has no stop or target of its own here: it simply follows the
buyer's entry and exit, so both sides of a signal are judged on exactly the same window. Seller margin is not modelled.

Result is stored on the trade as trade["sell_view"] = {"option", "key", "entry", "exit", "entry_ts", "exit_ts", "pct"}
where pct = (entry - exit) / entry * 100 (seller's return on the premium received, before costs). A lookup that fails is
retried on later runs up to MAX_TRIES times, then left as {"error": ..., "tries": n}.
"""
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
MAX_TRIES = 3
_EXIT_TS = {"SL_HIT": "sl_hit_time", "T1_THEN_SL": "sl_hit_time", "T1_THEN_TRAILING_SL": "sl_hit_time",
            "TRAILING_SL_HIT": "sl_hit_time", "T2_HIT": "t2_hit_time",
            "EOD_CLOSED": "last_processed_ts", "T1_HIT_THEN_EOD": "last_processed_ts"}


def _ts(x):
    try:
        dt = datetime.fromisoformat(str(x))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=IST)


def opposite(direction: str) -> str:
    return "PE" if direction == "CE" else "CE"


def exit_time(trade: dict):
    status = trade.get("final_status") or trade.get("status")
    field = _EXIT_TS.get(status)
    return trade.get(field) if field else None


def eligible(trade: dict) -> bool:
    """Entered and closed with a known exit minute; no completed or exhausted sell_view yet."""
    sv = trade.get("sell_view") or {}
    if "pct" in sv or sv.get("tries", 0) >= MAX_TRIES:
        return False
    return bool(trade.get("tracked_entry_price") and trade.get("actual_entry_time") and exit_time(trade))


def price_at(candles: list, when: datetime, max_gap_min: int = 5):
    """Close of the candle stamped `when`, else the first one after it, else the last one before it (within max_gap_min)."""
    rows = sorted(((_ts(c.get("timestamp")), c) for c in candles or []), key=lambda p: p[0] or datetime.min.replace(tzinfo=IST))
    rows = [(t, c) for t, c in rows if t is not None and c.get("close") is not None]
    after = [(t, c) for t, c in rows if t >= when]
    if after and (after[0][0] - when) <= timedelta(minutes=max_gap_min):
        return float(after[0][1]["close"])
    before = [(t, c) for t, c in rows if t <= when]
    if before and (when - before[-1][0]) <= timedelta(minutes=max_gap_min):
        return float(before[-1][1]["close"])
    return None


def compute(trade: dict, opp_key: str, candles: list) -> dict:
    t_in, t_out = _ts(trade.get("actual_entry_time")), _ts(exit_time(trade))
    if not opp_key:
        raise LookupError("opposite option not found")
    if t_in is None or t_out is None:
        raise LookupError("no entry/exit time")
    p_in, p_out = price_at(candles, t_in), price_at(candles, t_out)
    if not p_in or p_out is None:
        raise LookupError("no opposite-option candle at entry/exit")
    return {"option": f"{opposite(trade.get('direction'))} {trade.get('strike')}", "key": opp_key,
            "entry": round(p_in, 2), "exit": round(p_out, 2), "entry_ts": t_in.isoformat(), "exit_ts": t_out.isoformat(),
            "pct": round((p_in - p_out) / p_in * 100, 2)}


def attach_all(trades, opp_key_fn, candles_fn, log=None) -> int:
    """For each eligible trade: find the opposite option (opp_key_fn(trade) -> key or None), fetch its 1-minute candles from
    shortly before entry (candles_fn(key, since_iso) -> list; one fetch per key per call) and store trade["sell_view"].
    Never raises. Returns how many trades got a result."""
    done, cache = 0, {}
    for t in trades:
        try:
            if not eligible(t):
                continue
            key = opp_key_fn(t)
            since = (_ts(t["actual_entry_time"]) - timedelta(minutes=3)).isoformat()
            if key and (key not in cache or cache[key][0] > since):
                cache[key] = (since, candles_fn(key, since))
            t["sell_view"] = compute(t, key, cache[key][1] if key else [])
            done += 1
        except Exception as exc:
            tries = (t.get("sell_view") or {}).get("tries", 0) + 1
            t["sell_view"] = {"error": f"{type(exc).__name__}: {exc}"[:160], "tries": tries}
            if log:
                log.warning("SELL_VIEW %s failed (try %d): %s", t.get("trade_id"), tries, exc)
    return done


def pnl(trade: dict, lot, cost_pct: float):
    """(gross, net) rupees for ONE lot sold, or (None, None). Net subtracts the assumed round-trip cost on the sold premium."""
    sv = trade.get("sell_view") or {}
    if "pct" not in sv or not lot:
        return None, None
    gross = (sv["entry"] - sv["exit"]) * lot
    return round(gross), round(gross - sv["entry"] * lot * cost_pct / 100.0)


# --- opposite-option lookups ------------------------------------------------------------------------------------
def nse_opp_key_fn(master_rows: list):
    """Opposite CE/PE of the same underlying, expiry and strike from the Upstox instrument master rows."""
    idx = {}
    for r in master_rows or []:
        if r.get("option_type") in ("CE", "PE") and r.get("instrument_key") and r.get("strike_price") is not None:
            sym = r.get("underlying_symbol") or r.get("symbol")
            idx[(sym, r.get("expiry"), float(r["strike_price"]), r["option_type"])] = r["instrument_key"]

    def fn(trade):
        try:
            return idx.get((trade.get("symbol"), trade.get("expiry"), float(trade.get("strike")), opposite(trade.get("direction"))))
        except (TypeError, ValueError):
            return None
    return fn


def mcx_opp_key_fn(master_rows: list):
    """Opposite leg from the Angel One scrip master (strike stored x100, symbol ends with CE/PE)."""
    def fn(trade):
        want = opposite(trade.get("direction"))
        try:
            strike = float(trade.get("strike"))
            exp = datetime.strptime(str(trade.get("expiry")), "%Y-%m-%d").strftime("%d%b%Y").upper()
        except (TypeError, ValueError):
            return None
        for x in master_rows or []:
            if (x.get("exch_seg") == "MCX" and x.get("name") == trade.get("symbol") and x.get("instrumenttype") == "OPTFUT"
                    and str(x.get("expiry", "")).upper() == exp and str(x.get("symbol", "")).endswith(want)):
                try:
                    if abs(float(x["strike"]) / 100.0 - strike) < 1e-6:
                        return f"MCX|{x['token']}"
                except (KeyError, TypeError, ValueError):
                    continue
        return None
    return fn
