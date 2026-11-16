"""Tiered candle polling (2026-11-16) -- fewer broker calls with IDENTICAL results.

tracker.process_trade_candles() works purely on candle timestamps and always receives every candle since the trade's
last_processed_ts, so polling a trade every 15 minutes instead of every run changes only WHEN we learn about an exit,
never WHICH candle triggered it, the exit price, the outcome or the ledger row.

  Tier 1 (every run): live alerted trades, and trades whose notices go to Telegram (config.FOCUS_STRATEGIES when
          config.FOCUS_ONLY_NOTICES is on, otherwise every trade) -- an SL/T1/T2 notice must not wait.
  Tier 2 (every SHADOW_POLL_INTERVAL_MIN): data-only shadow trades. Also polled on every run in the closing window so the
          end-of-day close is applied, and on their first run after creation.
"""
import config
from datetime import datetime


def _hhmm(now):
    return (now.hour, now.minute)


def is_fast(trade: dict) -> bool:
    if not getattr(config, "FOCUS_ONLY_NOTICES", False):
        return True                                    # every trade sends notices -> nothing may be delayed
    import chart_notify
    return chart_notify.is_focus(trade)


def due(trade: dict, now: datetime, always_from=None, live: bool = False) -> bool:
    if live or not getattr(config, "SHADOW_TIERED_POLLING", True):
        return True
    interval = getattr(config, "SHADOW_POLL_INTERVAL_MIN", 14)
    if interval <= 0 or is_fast(trade):
        return True
    if always_from is not None and _hhmm(now) >= tuple(always_from):
        return True
    last = trade.get("last_polled_at")
    if not last:
        return True
    try:
        return (now - datetime.fromisoformat(last)).total_seconds() >= interval * 60
    except (TypeError, ValueError):
        return True


def mark(trade: dict, now: datetime) -> None:
    trade["last_polled_at"] = now.isoformat()
