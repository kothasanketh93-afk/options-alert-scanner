"""Send trade charts (trade_chart.py) to Telegram for NSE trades (added 2026-11-11). Every function here is best-effort:
it never raises, so a chart problem can never block an alert, the tracker or the run.

setup(trade)   -- once per alerted trade, right after its text alert: underlying 5-minute chart with the setup levels.
outcome(trade) -- once per alerted trade when it closes (SL / T2 / end of day): option premium 1-minute chart.
"""
from datetime import timedelta

import config
from logging_setup import get_logger

log = get_logger("chart_notify")
FINAL = {"SL_HIT", "T1_THEN_SL", "T1_THEN_TRAILING_SL", "TRAILING_SL_HIT", "T2_HIT", "EOD_CLOSED", "T1_HIT_THEN_EOD"}
_LABELS = {"sweep_fvg": "Sweep + FVG", "amd": "AMD", "oi_flow": "OI flow", "fvg": "Fair value gap", "ema_pb": "EMA pullback",
           "gap_orb": "Gap and go", "bos": "Break of structure", "order_block": "Order block", "trade_score": "Main scanner",
           "chase_bypass": "Chase bypass"}


def enabled() -> bool:
    return bool(getattr(config, "TRADE_CHARTS_ENABLED", True))


def strategy_label(trade: dict) -> str:
    eng, strat = trade.get("engine"), trade.get("strategy")
    name = _LABELS.get(strat, strat or "")
    return f"{eng.replace('_', ' ')} / {name}" if eng and name and strat not in ("trade_score", eng) else (name or eng or "")


def _lot(trade):
    try:
        import eod_report
        return eod_report.load_lot_sizes().get(trade.get("symbol"))
    except Exception:
        return None


def _send(path, caption):
    import telegram_notify
    telegram_notify.send_photo(path, caption=caption)


def setup(trade: dict, sender=None) -> bool:
    if not enabled() or trade.get("chart_setup_sent"):
        return False
    trade["chart_setup_sent"] = True                 # set first: one attempt per trade, never a retry loop
    try:
        import market_data
        import option_chain
        import trade_chart
        key = option_chain.resolve_underlying_key(trade["symbol"])
        day = str(trade.get("alert_time") or "")[:10]
        candles = [c for c in market_data.get_recent_underlying_candles(key, "5minute", 80) if c["timestamp"][:10] == day]
        pdh = pdl = None
        try:
            prev = [c for c in market_data.get_recent_daily_candles(key, 3) if str(c.get("timestamp", ""))[:10] < day]
            if prev:
                pdh, pdl = prev[-1]["high"], prev[-1]["low"]
        except Exception:
            pass
        path = trade_chart.temp_png(f"setup_{trade.get('trade_id', 'x')}")
        if not trade_chart.setup_chart(trade, candles, path, strategy=strategy_label(trade), pdh=pdh, pdl=pdl, lot=_lot(trade)):
            return False
        (sender or _send)(path, f"Setup chart: {trade['symbol']} {trade.get('strike')} {trade['direction']} ({strategy_label(trade)})")
        return True
    except Exception as exc:
        log.warning("CHART setup %s failed (%s: %s)", trade.get("trade_id"), type(exc).__name__, exc)
        return False


def outcome(trade: dict, sender=None) -> bool:
    status = trade.get("final_status") or trade.get("status")
    if not enabled() or trade.get("chart_outcome_sent") or status not in FINAL or not trade.get("tracked_entry_price"):
        return False
    trade["chart_outcome_sent"] = True
    try:
        import market_data
        import trade_chart
        since = (trade_chart._ts(trade["alert_time"]) - timedelta(minutes=10)).isoformat()
        candles = market_data.get_1min_candles_since(trade["instrument_key"], since)
        path = trade_chart.temp_png(f"result_{trade.get('trade_id', 'x')}")
        if not trade_chart.outcome_chart(trade, candles, path, strategy=strategy_label(trade), lot=_lot(trade)):
            return False
        (sender or _send)(path, f"Result chart: {trade['symbol']} {trade.get('strike')} {trade['direction']} -> {status.replace('_', ' ')}")
        return True
    except Exception as exc:
        log.warning("CHART outcome %s failed (%s: %s)", trade.get("trade_id"), type(exc).__name__, exc)
        return False
