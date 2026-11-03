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


def reward_risk(setup):
    """(t1_rr, t2_rr) measured at the realistic fill (the ask, else the alert premium). None if not computable."""
    fill = setup.get("ask") or setup.get("alert_premium")
    sl, t1, t2 = setup.get("stop_loss"), setup.get("target_1"), setup.get("target_2")
    if not fill or sl is None or t1 is None or fill <= sl:
        return None, None
    risk = fill - sl
    return round((t1 - fill) / risk, 2), (round((t2 - fill) / risk, 2) if t2 is not None else None)


def rr_check(setup):
    """Live alerts need first-target reward >= RISK_MIN_T1_RR x risk at the ask. Shadows are unaffected."""
    if not getattr(config, "RISK_GUARD_ENABLED", False):
        return True, ""
    t1rr, _ = reward_risk(setup)
    if t1rr is None:
        return True, ""   # cannot compute: do not block on missing data
    if t1rr < config.RISK_MIN_T1_RR:
        return False, f"T1 reward:risk {t1rr} below {config.RISK_MIN_T1_RR} at the ask"
    return True, ""
