"""
app/engines/decision_engine/risk.py -- Risk, Risk/Reward, and Position
Size Engines.
------------------------------------------------------------------------------
Entry/Stop/Target levels are NOT recomputed here -- levels.py's
dynamic_trade_levels() (or Early Breakout's own ATR-based levels) has
already priced entry_low/entry_high/stop_loss/target_1/target_2 onto
the trade dict upstream. This module scores the RISK implied by those
already-chosen levels and sizes the position against them -- it never
re-derives the levels themselves. (target.py separately offers an
optional Target 3 / probability-based target as an additive extra.)
"""

from config import (
    DECISION_ENGINE_MIN_RR, DECISION_ENGINE_POSITION_SIZE_MODE,
    DECISION_ENGINE_FIXED_QTY, DECISION_ENGINE_CAPITAL,
    DECISION_ENGINE_RISK_PCT_OF_CAPITAL, DECISION_ENGINE_CAPITAL_PCT_PER_TRADE,
    DECISION_ENGINE_LOT_SIZE_DEFAULT,
)


def _entry_reference(trade: dict) -> float:
    entry_low = trade.get("entry_low")
    entry_high = trade.get("entry_high")
    if entry_low is not None and entry_high is not None:
        return (entry_low + entry_high) / 2.0
    return trade.get("alert_premium") or 0.0


def calculate_risk(trade: dict) -> dict:
    entry = _entry_reference(trade)
    stop_loss = trade.get("stop_loss")
    target_1 = trade.get("target_1")
    target_2 = trade.get("target_2")

    if entry <= 0 or stop_loss is None:
        return {
            "max_risk_per_unit": None, "expected_reward_per_unit": None,
            "risk_score": 0.0, "capital_risk_pct": None, "warning": "Missing entry/stop levels",
        }

    # Premium-based instruments (options) always risk long-premium-style,
    # i.e. loss is bounded by (entry - stop) regardless of CE/PE, since
    # both legs are bought, not sold -- mirrors levels.dynamic_trade_levels()'s
    # own math (sl = premium - premium_atr * SL_ATR_MULT for both directions).
    max_risk_per_unit = max(0.0, entry - stop_loss)
    expected_reward_per_unit = max(0.0, (target_2 or target_1 or entry) - entry)

    risk_pct_of_entry = (max_risk_per_unit / entry) if entry else 0.0
    # Lower relative risk -> higher risk score (0-100, higher = safer).
    risk_score = max(0.0, min(100.0, 100.0 - risk_pct_of_entry * 200.0))

    capital = DECISION_ENGINE_CAPITAL
    capital_risk_pct = round((max_risk_per_unit * DECISION_ENGINE_LOT_SIZE_DEFAULT / capital) * 100, 2) if capital else None

    return {
        "max_risk_per_unit": round(max_risk_per_unit, 2),
        "expected_reward_per_unit": round(expected_reward_per_unit, 2),
        "risk_score": round(risk_score, 1),
        "capital_risk_pct": capital_risk_pct,
    }


def calculate_risk_reward(trade: dict) -> dict:
    entry = _entry_reference(trade)
    stop_loss = trade.get("stop_loss")
    target_1 = trade.get("target_1")
    target_2 = trade.get("target_2")

    risk_amount = (entry - stop_loss) if (entry and stop_loss is not None) else None
    reward_amount = ((target_2 or target_1) - entry) if (entry and (target_2 or target_1) is not None) else None

    if not risk_amount or risk_amount <= 0 or reward_amount is None:
        rr_ratio = 0.0
    else:
        rr_ratio = round(reward_amount / risk_amount, 2)

    passes_floor = rr_ratio >= DECISION_ENGINE_MIN_RR
    return {
        "entry": round(entry, 2) if entry else None,
        "stop_loss": stop_loss,
        "target_1": target_1,
        "target_2": target_2,
        "risk_reward_ratio": rr_ratio,
        "min_rr_threshold": DECISION_ENGINE_MIN_RR,
        "passes_rr_floor": passes_floor,
    }


def calculate_position_size(trade: dict, risk: dict) -> dict:
    mode = DECISION_ENGINE_POSITION_SIZE_MODE
    max_risk_per_unit = risk.get("max_risk_per_unit") or 0.0
    lot_size = DECISION_ENGINE_LOT_SIZE_DEFAULT

    if mode == "fixed_quantity":
        quantity = DECISION_ENGINE_FIXED_QTY
    elif mode == "capital_based":
        entry = _entry_reference(trade) or 1.0
        allocatable = DECISION_ENGINE_CAPITAL * DECISION_ENGINE_CAPITAL_PCT_PER_TRADE
        quantity = max(lot_size, int((allocatable / entry) // lot_size) * lot_size)
    elif mode == "risk_based":
        risk_budget = DECISION_ENGINE_CAPITAL * DECISION_ENGINE_RISK_PCT_OF_CAPITAL
        quantity = max(lot_size, int((risk_budget / max_risk_per_unit) // lot_size) * lot_size) if max_risk_per_unit > 0 else lot_size
    elif mode == "percentage_based":
        entry = _entry_reference(trade) or 1.0
        allocatable = DECISION_ENGINE_CAPITAL * DECISION_ENGINE_CAPITAL_PCT_PER_TRADE
        quantity = max(lot_size, int((allocatable / entry) // lot_size) * lot_size)
    else:  # "dynamic" -- blend risk-based and capital-based, take the smaller (more conservative)
        entry = _entry_reference(trade) or 1.0
        allocatable = DECISION_ENGINE_CAPITAL * DECISION_ENGINE_CAPITAL_PCT_PER_TRADE
        capital_qty = max(lot_size, int((allocatable / entry) // lot_size) * lot_size)
        risk_budget = DECISION_ENGINE_CAPITAL * DECISION_ENGINE_RISK_PCT_OF_CAPITAL
        risk_qty = max(lot_size, int((risk_budget / max_risk_per_unit) // lot_size) * lot_size) if max_risk_per_unit > 0 else capital_qty
        quantity = min(capital_qty, risk_qty)

    return {"mode": mode, "quantity": int(quantity), "lot_size": lot_size}
