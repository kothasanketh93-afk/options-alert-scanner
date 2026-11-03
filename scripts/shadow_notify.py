"""
Shadow Telegram notices (2026-11-02, user request: "we need at least shadow alerts").
-----------------------------------------------------------------------------------
Informational messages for SHADOW setups only. Clearly labelled NOT A TRADE SIGNAL: these setups
have no validated probability (PROBABILITY gate), they exist to collect evidence. Never touches
the live alert path, never changes a gate. Fully isolated: any failure is logged and swallowed.

* Creation notice: one per (engine/strategy, symbol, direction, day) -- the ITM/ATM/OTM variants of
  the same setup do not spam. The representative trade is flagged trade["shadow_notified"]=True.
* Outcome notice: only for flagged trades, on SL_HIT / T1_HIT / T2_HIT / EOD_CLOSED / NOT_ENTERED.
"""
import config
from logging_setup import get_logger

log = get_logger("shadow_notify")
OUTCOME_LABEL = {"SL_HIT": "🔴 Stop-loss hit", "T1_HIT": "🟢 Target 1 hit", "T2_HIT": "🟢 Target 2 hit",
                 "EOD_CLOSED": "⚪ Closed at end of day", "NOT_ENTERED": "⚪ Entry never triggered"}
_DISCLAIMER = "🧪 SHADOW – data collection only, NOT a trade signal."


def _fmt(x):
    return "-" if x is None else (f"{x:.2f}" if isinstance(x, (int, float)) else str(x))


def _label(trade: dict) -> str:
    """Engine + strategy for display/dedupe (fib_pivot shadows carry the default strategy 'trade_score')."""
    eng, strat = trade.get("engine"), trade.get("strategy")
    if eng and strat and strat not in ("trade_score", eng):
        return f"{eng}/{strat}"
    return eng or strat or "scanner"


def format_created(trade: dict) -> str:
    strat = _label(trade)
    return (f"{_DISCLAIMER}\n<b>{trade['symbol']} {trade['direction']} {trade.get('strike')}</b> "
            f"({trade.get('moneyness', '')}) – {strat}\n"
            f"Premium: {_fmt(trade.get('alert_premium'))} | SL: {_fmt(trade.get('stop_loss'))} | "
            f"T1: {_fmt(trade.get('target_1'))} | T2: {_fmt(trade.get('target_2'))}\n"
            f"Score: {_fmt(trade.get('trade_score'))}/100 | Grade: {trade.get('grade')}"
            + (f" | Spread: {_fmt(trade.get('spread_pct'))}%" if trade.get("spread_pct") is not None else "")
            + f"\nWhy: {'; '.join((trade.get('why_reasons') or [])[:3])}".rstrip())


def format_outcome(trade: dict, etype: str) -> str:
    return (f"{_DISCLAIMER}\n{OUTCOME_LABEL[etype]}: <b>{trade['symbol']} {trade['direction']} "
            f"{trade.get('strike')}</b> ({_label(trade)})\n"
            f"Entry: {_fmt(trade.get('tracked_entry_price'))} | SL: {_fmt(trade.get('stop_loss'))} | "
            f"T2: {_fmt(trade.get('target_2'))}")


def _send(text):
    import telegram_notify
    telegram_notify.send(text)


def notify_created(state: dict, trade: dict, now, sender=None) -> bool:
    if not getattr(config, "SHADOW_TELEGRAM_ENABLED", False):
        return False
    if getattr(config, "CONDITIONS_ALERT_MODE", False) and not trade.get("engine"):
        return False   # main-engine setups now alert live; no duplicate shadow message
    try:
        day = now.strftime("%Y-%m-%d")
        key = "|".join(str(x) for x in (day, _label(trade),
                                        trade.get("symbol"), trade.get("direction")))
        seen = state.setdefault("shadow_notified_keys", [])
        seen[:] = [k for k in seen if k.startswith(day)]          # keep today's keys only
        if key in seen:
            return False
        (sender or _send)(format_created(trade))
        seen.append(key)
        trade["shadow_notified"] = True
        return True
    except Exception as exc:
        log.error("SHADOW_NOTIFY created failed (%s: %s)", type(exc).__name__, exc)
        return False


def notify_events(trade: dict, events: list, sender=None) -> int:
    if not getattr(config, "SHADOW_TELEGRAM_ENABLED", False) or not trade.get("shadow_notified"):
        return 0
    sent = 0
    for ev in events or []:
        etype = ev.get("type")
        if etype not in OUTCOME_LABEL:
            continue
        try:
            (sender or _send)(format_outcome(trade, etype))
            sent += 1
        except Exception as exc:
            log.error("SHADOW_NOTIFY outcome failed (%s: %s)", type(exc).__name__, exc)
    return sent
