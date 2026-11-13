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
           "gap_orb": "Gap and go", "bos": "Break of structure", "order_block": "Order block", "pdhl_retest": "PDH/PDL retest", "vwap_pb": "VWAP pullback", "trade_score": "Main scanner",
           "chase_bypass": "Chase bypass"}


# Charts (and the AI tutor call inside them) can take several seconds each. While DEFER is on, runners queue them and
# call flush() after every alert of the run has gone out, so a chart never delays another trade's alert.
DEFER = False
_QUEUE = []


def later(fn, *args, **kwargs) -> bool:
    """Run fn now, or queue it while DEFER is on. Returns fn's result, or True when queued."""
    if DEFER:
        _QUEUE.append((fn, args, kwargs))
        return True
    return fn(*args, **kwargs)


def flush() -> int:
    """Run queued chart jobs (each best-effort). Returns how many ran."""
    n = 0
    while _QUEUE:
        fn, args, kwargs = _QUEUE.pop(0)
        try:
            fn(*args, **kwargs)
        except Exception as exc:
            log.warning("CHART job failed (%s: %s)", type(exc).__name__, exc)
        n += 1
    return n


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


def nse_underlying(trade: dict) -> list:
    """Today's 5-minute candles of the trade's underlying (cached per run by market_data); [] on any problem."""
    try:
        import market_data
        import option_chain
        key = option_chain.resolve_underlying_key(trade["symbol"])
        day = str(trade.get("alert_time") or "")[:10]
        return [c for c in market_data.get_recent_underlying_candles(key, "5minute", 80) if c["timestamp"][:10] == day]
    except Exception as exc:
        log.warning("CHART underlying %s failed (%s: %s)", trade.get("symbol"), type(exc).__name__, exc)
        return []


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
        if not trade_chart.outcome_chart(trade, candles, path, strategy=strategy_label(trade), lot=_lot(trade),
                                         underlying=nse_underlying(trade)):
            return False
        (sender or _send)(path, f"Result chart: {trade['symbol']} {trade.get('strike')} {trade['direction']} -> {status.replace('_', ' ')}")
        return True
    except Exception as exc:
        log.warning("CHART outcome %s failed (%s: %s)", trade.get("trade_id"), type(exc).__name__, exc)
        return False


def lesson_card(trades: list, date_str: str, market: str, fetch_candles, send_text, send_photo, lot_fn=None,
                name_fn=None, label_fn=None, fetch_underlying=None) -> bool:
    """Daily lesson card. Telegram text and captions in English; the teaching itself (Telugu) is inside the images:
    one lesson-card image (AI tutor when available, else rule-based), then the best and the worst trade as result charts.
    fetch_candles(trade) -> option 1-minute candles. Never raises; returns True when the summary was sent."""
    import lessons
    import outcomes_ledger
    import trade_chart
    from html import escape
    try:
        closed = [t for t in trades if (t.get("final_status") or t.get("status")) in FINAL and t.get("tracked_entry_price")]
        focus = [t for t in closed if is_focus(t)]
        pool = focus or closed
        res = [(outcomes_ledger.result_pct(t), t) for t in pool]
        res = [(r, t) for r, t in res if r is not None]
        if not res:
            return False
        wins = sum(1 for r, _t in res if r > 0)
        rupees, rows = 0.0, []
        for r, t in res:
            lot = lot_fn(t) if lot_fn else None
            pnl = t["tracked_entry_price"] * lot * r / 100.0 if lot else None
            rupees += pnl or 0.0
            rows.append({"symbol": (name_fn(t) if name_fn else t.get("symbol")), "strike": t.get("strike"),
                         "direction": t.get("direction"), "strategy": label_fn(t) if label_fn else strategy_label(t),
                         "alert_time": str(t.get("alert_time") or "")[11:16], "status": t.get("final_status") or t.get("status"),
                         "result_pct": r, "one_lot_rs": round(pnl) if pnl is not None else None, "ai_verdict": t.get("ai_verdict"),
                         "observations": [x for x, k in lessons.review_lines(t, lot) if k != "head"][1:]})
        scope = "focus strategies" if focus else "all strategies"
        send_text(f"📚 <b>Daily lesson – {escape(market)} {escape(date_str)}</b>\n"
                  f"{len(res)} closed trades ({scope}): {wins} won, {len(res) - wins} lost"
                  + (f", one lot each Rs {rupees:+,.0f} before costs" if rupees else "")
                  + "\nThe lesson card and the best/worst trade charts follow.")
        import ai_tutor
        lines = ai_tutor.day_guidance({"market": market, "date": date_str, "trades": rows,
                                       "wins": wins, "losses": len(res) - wins, "one_lot_total_rs": round(rupees)})
        if not lines:
            rep = lessons.day_lessons([t for _r, t in res])
            lines = [("ఈ రోజు ఒక్క చూపులో", "head"),
                     (f"{len(res)} trades: {wins} లాభం, {len(res) - wins} నష్టం" + (f", 1 lot చొప్పున Rs {rupees:+,.0f}" if rupees else ""), "plain")]
            if rep:
                lines.append(("మళ్ళీ మళ్ళీ కనిపించినవి", "head"))
                lines += [(f"({n} trades) {text}", "warn") for n, text in rep]
            lines += [("ఈ రోజు పని (15 నిమిషాలు)", "head"),
                      ("ఉత్తమ trade chart చూడండి: entry కి ముందు ఏం కనిపించింది?", "plain"),
                      ("చెత్త trade chart చూడండి: ఏ లైన్ దగ్గర idea తప్పని తెలిసింది?", "plain"),
                      ("ఒక వాక్యం రాసుకోండి: రేపు ఏ ఒక్క తప్పు చేయకూడదు?", "plain")]
        card = trade_chart.temp_png(f"lesson_{market}_{date_str}")
        if trade_chart.text_card(card, f"ఈ రోజు పాఠం – {market} {date_str}",
                                 (f"AI tutor ({ai_tutor.provider()})" if ai_tutor.enabled() else "rule-based") + f"  |  {len(res)} trades, {scope}", lines):
            send_photo(card, f"Lesson card {market} {date_str}")
        res.sort(key=lambda x: x[0])
        picks = [("Best trade today", res[-1][1])] + ([("Worst trade today", res[0][1])] if len(res) > 1 else [])
        for title, t in picks:
            try:
                path = trade_chart.temp_png(f"lesson_{t.get('trade_id', 'x')}")
                nm = name_fn(t) if name_fn else None
                if trade_chart.outcome_chart(t, fetch_candles(t), path, name=nm,
                                             strategy=label_fn(t) if label_fn else strategy_label(t),
                                             lot=lot_fn(t) if lot_fn else None,
                                             underlying=fetch_underlying(t) if fetch_underlying else None):
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
                       lambda p, c: telegram_notify.send_photo(p, caption=c), lot_fn=_lot, fetch_underlying=nse_underlying)
