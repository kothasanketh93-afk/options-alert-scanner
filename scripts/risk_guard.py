"""Pre-alert risk limits for LIVE (tiered) alerts. Shadow / data-collection paths never call this.

Limits (see docs plan "Risk framework"): no new alert after RISK_ENTRY_CUTOFF_HHMM, at most
RISK_MAX_OPEN_TRADES open at once, one open trade per underlying, and a daily stop after
RISK_DAILY_SL_STOP stop-losses. Returns (allowed, reason).
"""
import config

LOSS_STATUSES = ("SL_HIT", "TRAILING_SL_HIT", "T1_THEN_SL")
OPEN_STATUSES = ("WAITING_ENTRY", "ACTIVE", "T1_HIT")


def check_global(state, now):
    """Restrictions that apply to every candidate; if one trips, selection stops for the run."""
    if getattr(config, "RISK_KILL_SWITCH", False):
        return False, "kill switch is ON"
    if not getattr(config, "RISK_GUARD_ENABLED", False):
        return True, ""
    hh, mm = config.RISK_ENTRY_CUTOFF_HHMM
    if (now.hour, now.minute) >= (hh, mm):
        return False, f"after entry cutoff {hh:02d}:{mm:02d}"
    trades = (state.get("trades") or {}).values()
    open_trades = [t for t in trades if t.get("status") in OPEN_STATUSES]
    if len(open_trades) >= config.RISK_MAX_OPEN_TRADES:
        return False, f"{len(open_trades)} open trades (max {config.RISK_MAX_OPEN_TRADES})"
    cap = getattr(config, "RISK_MAX_TRADES_PER_DAY", None)
    if cap and len(list(trades)) >= cap:
        return False, f"{len(list(trades))} live alerts today (max {cap})"
    losses = sum(1 for t in trades if (t.get("final_status") or t.get("status")) in LOSS_STATUSES)
    if losses >= config.RISK_DAILY_SL_STOP:
        return False, f"daily stop: {losses} stop-losses today"
    return True, ""


def check_symbol(state, symbol):
    """Restrictions specific to one underlying; a failing candidate must not stop the next one being evaluated."""
    if not getattr(config, "RISK_GUARD_ENABLED", False):
        return True, ""
    if any(t.get("symbol") == symbol and t.get("status") in OPEN_STATUSES for t in (state.get("trades") or {}).values()):
        return False, f"{symbol} already has an open trade"
    return True, ""


def check(state, symbol, now):
    """Both layers (kept for callers/tests that want one answer)."""
    ok, why = check_global(state, now)
    return (ok, why) if not ok else check_symbol(state, symbol)


def size_trade(setup, lot_size, capital=None):
    """One-lot planned risk against the per-trade budget. Fill = ask (else alert premium).

    planned loss = (fill - SL) x lot  +  round-trip slippage allowance  +  assumed round-trip cost.
    Returns a dict, or None when it cannot be computed (no lot size / prices). Never raises.
    """
    try:
        fill = setup.get("ask") or setup.get("alert_premium")
        sl = setup.get("stop_loss")
        if not (lot_size and fill and sl is not None) or fill <= sl:
            return None
        cap = capital if capital is not None else config.PAPER_CAPITAL
        budget = cap * config.RISK_PER_TRADE_PCT / 100.0
        stop_loss_rs = (fill - sl) * lot_size
        slip_rs = fill * lot_size * 2 * config.EXEC_SLIPPAGE_PCT / 100.0
        cost_rs = fill * lot_size * config.LEDGER_ROUND_TRIP_COST_PCT / 100.0
        lot_risk = stop_loss_rs + slip_rs + cost_rs
        return {"fill": round(fill, 2), "lot_size": lot_size, "premium_rs": round(fill * lot_size),
                "lot_risk_rs": round(lot_risk), "budget_rs": round(budget), "capital": cap,
                "within_budget": lot_risk <= budget, "lots_allowed": int(budget // lot_risk) if lot_risk > 0 else 0,
                "premium_pct_of_capital": round(fill * lot_size / cap * 100, 1)}
    except Exception:
        return None


def sizing_check(setup, lot_size):
    """(ok, reason, sizing). Blocks only when RISK_SIZING_ENFORCE is on; the sizing dict is always returned for display."""
    sz = size_trade(setup, lot_size)
    if sz is None or sz["within_budget"] or not getattr(config, "RISK_SIZING_ENFORCE", False):
        return True, "", sz
    return False, (f"one lot risks Rs {sz['lot_risk_rs']:,} > budget Rs {sz['budget_rs']:,} "
                   f"({config.RISK_PER_TRADE_PCT}% of Rs {sz['capital']:,})"), sz


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
    if not getattr(config, "RISK_GUARD_ENABLED", False) or not getattr(config, "RISK_RR_GATE_ENABLED", False):
        return True, ""
    t1rr, _ = reward_risk(setup)
    if t1rr is None:
        return True, ""   # cannot compute: do not block on missing data
    if t1rr < config.RISK_MIN_T1_RR:
        return False, f"T1 reward:risk {t1rr} below {config.RISK_MIN_T1_RR} at the ask"
    return True, ""
