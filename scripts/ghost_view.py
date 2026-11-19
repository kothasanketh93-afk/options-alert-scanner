"""Direction check for NOT_ENTERED trades (added 2026-11-19). Data collection only: no alerts, never changes a trade's outcome.

A NOT_ENTERED setup never got its entry fill, so it has no P&L. But the signal still made a call on direction. After the
session closes this module replays the bought option's 1-minute candles from the alert minute to the close and records which
level the option touched FIRST, using the trade's own levels:

  T1_FIRST   target_1 touched before stop_loss   -> the direction was right
  SL_FIRST   stop_loss touched before target_1   -> the direction was wrong
  BOTH       the same candle touched both        -> unknown order
  NEITHER    neither level touched by the close

Stored as trade["ghost_view"] = {"result", "mfe_pct", "mae_pct", "ref", "tries"}; mfe/mae are % moves from alert_premium.
A failed lookup is retried on later runs up to MAX_TRIES times. The ledger row for a NOT_ENTERED trade is held back until
this ran (or the grace window ended), see hold_row().
"""
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
MAX_TRIES = 3
GRACE_MIN = 45        # after the close, write the ledger row even without a ghost result


def _ts(x):
    try:
        dt = datetime.fromisoformat(str(x))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=IST)


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _close_dt(trade: dict, close_default=(15, 30)):
    at = _ts(trade.get("alert_time"))
    if at is None:
        return None
    h, m = tuple(trade.get("market_close_hhmm") or close_default)
    return at.replace(hour=h, minute=m, second=0, microsecond=0)


def _is_ne(trade: dict) -> bool:
    return (trade.get("final_status") or trade.get("status")) == "NOT_ENTERED"


def eligible(trade: dict, now: datetime, close_default=(15, 30)) -> bool:
    if not _is_ne(trade) or not trade.get("instrument_key"):
        return False
    gv = trade.get("ghost_view") or {}
    if "result" in gv or gv.get("tries", 0) >= MAX_TRIES:
        return False
    close = _close_dt(trade, close_default)
    if close is None or now < close:
        return False
    return all(_f(trade.get(k)) for k in ("alert_premium", "stop_loss", "target_1")) and _ts(trade.get("alert_time")) is not None


def hold_row(trade: dict, now: datetime, close_default=(15, 30)) -> bool:
    """True while the ledger should wait for the ghost result of this NOT_ENTERED trade."""
    if not _is_ne(trade) or "result" in (trade.get("ghost_view") or {}):
        return False
    if (trade.get("ghost_view") or {}).get("tries", 0) >= MAX_TRIES or not trade.get("instrument_key"):
        return False
    close = _close_dt(trade, close_default)
    if close is None:
        return False
    return now < close + timedelta(minutes=GRACE_MIN)


def compute(trade: dict, candles: list, close_default=(15, 30)) -> dict:
    ref, sl, t1 = _f(trade.get("alert_premium")), _f(trade.get("stop_loss")), _f(trade.get("target_1"))
    start, close = _ts(trade.get("alert_time")), _close_dt(trade, close_default)
    if not (ref and sl and t1) or start is None:
        raise LookupError("missing levels")
    rows = []
    for c in candles or []:
        t = _ts(c.get("timestamp"))
        if t is not None and t >= start.replace(second=0, microsecond=0) and (close is None or t < close):
            rows.append((t, _f(c.get("high")), _f(c.get("low"))))
    rows = sorted(r for r in rows if r[1] is not None and r[2] is not None)
    if not rows:
        raise LookupError("no candles after alert")
    result, hi, lo = "NEITHER", rows[0][1], rows[0][2]
    for _t, h, l in rows:
        hi, lo = max(hi, h), min(lo, l)
        if result == "NEITHER":
            up, dn = h >= t1, l <= sl
            if up and dn:
                result = "BOTH"
            elif up:
                result = "T1_FIRST"
            elif dn:
                result = "SL_FIRST"
    return {"result": result, "mfe_pct": round((hi - ref) / ref * 100, 2), "mae_pct": round((lo - ref) / ref * 100, 2),
            "ref": round(ref, 2)}


def attach_all(trades, candles_fn, now: datetime, log=None, close_default=(15, 30)) -> int:
    """Fetch candles (candles_fn(key, since_iso) -> list) once per eligible trade and store trade["ghost_view"]. Never raises."""
    done = 0
    for t in trades:
        try:
            if not eligible(t, now, close_default):
                continue
            since = (_ts(t["alert_time"]) - timedelta(minutes=1)).isoformat()
            t["ghost_view"] = compute(t, candles_fn(t["instrument_key"], since), close_default)
            done += 1
        except Exception as exc:
            tries = (t.get("ghost_view") or {}).get("tries", 0) + 1
            t["ghost_view"] = {"error": f"{type(exc).__name__}: {exc}"[:160], "tries": tries}
            if log:
                log.warning("GHOST_VIEW %s failed (try %d): %s", t.get("trade_id"), tries, exc)
    return done
