"""Run-gap detector: tells you when the scanner's external trigger skipped runs during market hours.

It can only fire when a run happens AFTER the gap (it is a 'you were blind from A to B' notice). A total outage
cannot report itself -- enable failure notifications on the external cron service for that case.
"""
from datetime import datetime

import config
import telegram_notify as tg

_OPEN, _CLOSE = (9, 20), (15, 25)


def _in_market(dt: datetime) -> bool:
    return dt.weekday() < 5 and _OPEN <= (dt.hour, dt.minute) <= _CLOSE


def check(state: dict, now: datetime, send=None) -> bool:
    """Record this run's time in state; alert (once) if the previous run was > RUN_GAP_ALERT_MIN ago in market hours."""
    send = send or tg.send
    prev_s = state.get("last_run_at")
    state["last_run_at"] = now.isoformat(timespec="seconds")
    if not getattr(config, "RUN_GAP_ALERT_ENABLED", True) or not prev_s:
        return False
    try:
        prev = datetime.fromisoformat(prev_s)
        if prev.tzinfo is None and now.tzinfo is not None:
            prev = prev.replace(tzinfo=now.tzinfo)
        gap_min = (now - prev).total_seconds() / 60.0
    except Exception:
        return False
    if gap_min <= config.RUN_GAP_ALERT_MIN or not (_in_market(prev) and _in_market(now)):
        return False
    state["run_gaps"] = int(state.get("run_gaps", 0)) + 1
    open_n = sum(1 for k in ("trades", "shadow_trades", "fib_pivot_shadow_trades", "orb_pivot_shadow_trades", "sweep_shadow_trades")
                 for t in (state.get(k) or {}).values() if (t.get("final_status") or t.get("status")) in ("ACTIVE", "T1_HIT", "OPEN"))
    try:
        send(f"⚠️ <b>Scanner gap</b>\nNo run between {prev.strftime('%H:%M')} and {now.strftime('%H:%M')} IST "
             f"({gap_min:.0f} min). Open tracked trades during the gap: {open_n}. Prices in between were not scanned live "
             f"(the tracker replays 1-min candles, so outcomes are still captured). Check the external cron trigger.")
    except Exception:
        return False
    return True
