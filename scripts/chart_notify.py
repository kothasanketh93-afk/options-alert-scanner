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


def focus_key(trade: dict) -> str:
    strat = trade.get("strategy") or "trade_score"
    return f"{trade['engine']}:{strat}" if trade.get("engine") else strat


def is_focus(trade: dict) -> bool:
    focus = getattr(config, "FOCUS_STRATEGIES", None)
    return True if not focus else focus_key(trade) in focus


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
    if not enabled() or trade.get("chart_setup_sent") or not is_focus(trade):
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
    if (not enabled() or trade.get("chart_outcome_sent") or status not in FINAL or not trade.get("tracked_entry_price")
            or not is_focus(trade)):
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


def lesson_card(trades: list, date_str: str, market: str, fetch_candles, send_text, send_photo, lot_fn=None,
                name_fn=None, label_fn=None) -> bool:
    """Daily Telugu lesson card: today's focus trades in one message (count, wins, one-lot result), the mistakes that
    repeated, then the best and the worst trade as result charts. fetch_candles(trade) -> option 1-minute candles.
    Never raises; returns True when the text part was sent."""
    import lessons
    import outcomes_ledger
    import trade_chart
    from html import escape
    try:
        closed = [t for t in trades if (t.get("final_status") or t.get("status")) in FINAL and t.get("tracked_entry_price")]
        focus = [t for t in closed if is_focus(t)]
        pool = focus or closed
        if not pool:
            return False
        res = [(outcomes_ledger.result_pct(t), t) for t in pool]
        res = [(r, t) for r, t in res if r is not None]
        if not res:
            return False
        wins = sum(1 for r, _t in res if r > 0)
        rupees = 0.0
        for r, t in res:
            lot = lot_fn(t) if lot_fn else None
            if lot:
                rupees += t["tracked_entry_price"] * lot * r / 100.0
        lines = [f"📚 <b>ఈ రోజు పాఠం – {escape(market)} {escape(date_str)}</b>",
                 f"{'Focus strategies' if focus else 'అన్ని strategies'}: {len(res)} trades, {wins} లాభం, {len(res) - wins} నష్టం"
                 + (f", 1 lot చొప్పున మొత్తం Rs {rupees:+,.0f} (costs ముందు)" if rupees else "")]
        rep = lessons.day_lessons([t for _r, t in res])
        if rep:
            lines.append("\n<b>ఈ రోజు మళ్ళీ మళ్ళీ కనిపించినవి</b>")
            lines += [f"• ({n} trades) {escape(text)}" for n, text in rep]
        lines.append("\n<b>ఈ రోజు పని (15 నిమిషాలు)</b>\n1. కింద ఉన్న ఉత్తమ trade chart చూడండి: entry కి ముందు ఏం కనిపించింది?"
                     "\n2. చెత్త trade chart చూడండి: ఏ లైన్ దగ్గర idea తప్పని తెలిసింది?"
                     "\n3. ఒక వాక్యం రాసుకోండి: రేపు ఏ ఒక్క తప్పు చేయకూడదు?")
        send_text("\n".join(lines))
        res.sort(key=lambda x: x[0])
        picks = [("ఈ రోజు ఉత్తమ trade 🟢", res[-1][1])] + ([("ఈ రోజు చెత్త trade 🔴", res[0][1])] if len(res) > 1 else [])
        for title, t in picks:
            try:
                path = trade_chart.temp_png(f"lesson_{t.get('trade_id', 'x')}")
                nm = name_fn(t) if name_fn else None
                if trade_chart.outcome_chart(t, fetch_candles(t), path, name=nm,
                                             strategy=label_fn(t) if label_fn else strategy_label(t),
                                             lot=lot_fn(t) if lot_fn else None):
                    send_photo(path, f"{title}: {nm or t.get('symbol')} {t.get('strike')} {t.get('direction')}")
            except Exception as exc:
                log.warning("LESSON chart %s failed (%s: %s)", t.get("trade_id"), type(exc).__name__, exc)
        return True
    except Exception as exc:
        log.warning("LESSON card failed (%s: %s)", type(exc).__name__, exc)
        return False


def nse_lesson_card(state: dict) -> bool:
    """NSE lesson card from every engine's trades today (once per day)."""
    if state.get("lesson_card_sent") or not enabled():
        return False
    state["lesson_card_sent"] = True
    import market_data
    import outcomes_ledger
    import telegram_notify
    trades = [t for key, _e in outcomes_ledger.STATE_KEYS for t in (state.get(key) or {}).values()]
    seen, uniq = set(), []
    for t in trades:                       # a shadow copy of a live trade is the same lesson
        k = (t.get("symbol"), t.get("direction"), t.get("strike"), t.get("alert_time"))
        if k not in seen:
            seen.add(k)
            uniq.append(t)

    def candles(t):
        import trade_chart
        return market_data.get_1min_candles_since(t["instrument_key"],
                                                  (trade_chart._ts(t["alert_time"]) - timedelta(minutes=10)).isoformat())
    return lesson_card(uniq, state["date"], "NSE", candles, telegram_notify.send,
                       lambda p, c: telegram_notify.send_photo(p, caption=c), lot_fn=_lot)
