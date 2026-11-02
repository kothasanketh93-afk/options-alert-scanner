"""Pre-alert risk limits for LIVE (tiered) alerts. Shadow / data-collection paths never call this.

Limits (see docs plan "Risk framework"): no new alert after RISK_ENTRY_CUTOFF_HHMM, at most
RISK_MAX_OPEN_TRADES open at once, one open trade per underlying, and a daily stop after
RISK_DAILY_SL_STOP stop-losses. Returns (allowed, reason).
"""
import config

LOSS_STATUSES = ("SL_HIT", "TRAILING_SL_HIT", "T1_THEN_SL")
OPEN_STATUSES = ("WAITING_ENTRY", "ACTIVE", "T1_HIT")


def check(state, symbol, now):
    if not getattr(config, "RISK_GUARD_ENABLED", False):
        return True, ""
    hh, mm = config.RISK_ENTRY_CUTOFF_HHMM
    if (now.hour, now.minute) >= (hh, mm):
        return False, f"after entry cutoff {hh:02d}:{mm:02d}"
    trades = (state.get("trades") or {}).values()
    open_trades = [t for t in trades if t.get("status") in OPEN_STATUSES]
    if len(open_trades) >= config.RISK_MAX_OPEN_TRADES:
        return False, f"{len(open_trades)} open trades (max {config.RISK_MAX_OPEN_TRADES})"
    if any(t.get("symbol") == symbol for t in open_trades):
        return False, f"{symbol} already has an open trade"
    losses = sum(1 for t in trades if (t.get("final_status") or t.get("status")) in LOSS_STATUSES)
    if losses >= config.RISK_DAILY_SL_STOP:
        return False, f"daily stop: {losses} stop-losses today"
    return True, ""
