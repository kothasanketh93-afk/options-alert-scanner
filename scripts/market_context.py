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
