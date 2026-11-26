"""RFP (Regime-First Pullback) NSE shadow engine entry point. main.create_rfp_shadow_candidates() is the only caller. See rfp.py."""
from datetime import datetime, timedelta, timezone

import config
import market_data
import rfp
from indicators import compute_atr
from logging_setup import get_logger
from option_chain import resolve_underlying_key, get_atm_strike, current_expiry, _extract_leg
from filters import liquidity_ok
from levels import dynamic_trade_levels, estimated_holding_minutes
from scanner import current_session
from app.engines.sweep_reversal import signals as base

_logger = get_logger("rfp_nse")


def _build_setup(symbol, sig, atr, session):
    direction = sig["direction"]
    underlying_key = resolve_underlying_key(symbol)
    atm_strike, spot_price = get_atm_strike(symbol)
    expiry = current_expiry(symbol)
    today = datetime.now(timezone(timedelta(hours=5, minutes=30))).strftime("%Y-%m-%d")
    if expiry == today:
        _logger.info("RFP_REJECTED %s %s: expiry day", symbol, direction)
        return None
    chain = market_data.get_option_chain(underlying_key, expiry)
    leg = _extract_leg(chain, atm_strike, direction)
    if leg is None:
        return None
    premium, delta = leg["ltp"], leg["delta"]
    if premium is None or delta is None or delta == 0:
        return None
    if not liquidity_ok(leg["oi"], leg["volume"], leg["bid"], leg["ask"], premium, symbol):
        _logger.info("RFP_REJECTED %s %s: liquidity gate failed", symbol, direction)
        return None
    zone = dynamic_trade_levels(premium, atr, delta, direction, spot_price, None)
    lv = base.option_levels(premium, delta, sig["signal_close"], sig["extreme"], atr, direction)
    rr = (lv["target_1"] - premium) / (premium - lv["stop_loss"]) if premium > lv["stop_loss"] else 0.0
    reasons = {
        "regime": f"{sig['regime']} day (score {sig['regime_score']}, votes {sig['regime_votes']})",
        "setup": f"{sig['level_name']} pullback, trigger close {sig['signal_close']}",
        "exit": f"stop to entry after +{sig['be_trigger_pct']}%",
        "risk_reward": f"1:{rr:.1f}",
    }
    why = [f"✓ {v}" for v in reasons.values()]
    return {
        "strategy": "rfp", "symbol": symbol, "direction": direction, "moneyness": "ATM",
        "instrument_key": leg["instrument_key"], "strike": atm_strike, "expiry": expiry,
        "spot_price": spot_price, "alert_premium": premium,
        "entry_low": zone["entry_low"], "entry_high": zone["entry_high"],
        "stop_loss": lv["stop_loss"], "target_1": lv["target_1"], "target_2": lv["target_2"],
        "grade": "Shadow", "trade_score": 50, "probability_pct": None, "probability_label": "Shadow",
        "score_breakdown": [f"{k.title()}: {v}" for k, v in reasons.items()],
        "confidence_score": 5.0, "risk_score": round(min(10.0, rr * 3), 1),
        "reward_score": round(min(10.0, rr * 4), 1), "expected_rr": f"1:{rr:.1f}",
        "factor_checklist": why, "confidence_stars": "☆☆☆☆☆", "confidence_label": "Shadow",
        "risk_rating": "🟡", "risk_label": "Unvalidated",
        "expected_holding_minutes": estimated_holding_minutes(atr, atr), "why_reasons": why,
        "session": session, "alternatives": [],
        "pivot_level_name": sig["level_name"], "pivot_level_value": sig["level_value"],
        "atr": round(atr, 2), "be_trigger_pct": sig["be_trigger_pct"],
        "regime": sig["regime"], "regime_score": sig["regime_score"],
        "entry_oi": leg.get("oi"), "entry_volume": leg.get("volume"),
        "spread_pct": (round((leg["ask"] - leg["bid"]) / premium * 100, 2)
                       if leg.get("bid") and leg.get("ask") else None),
    }


def _scan_symbol(symbol, session):
    key = resolve_underlying_key(symbol)
    if not key:
        return []
    today = market_data.get_recent_underlying_candles(key, "5minute", 80)
    if not today:
        return []
    day = str(today[-1]["timestamp"])[:10]
    today = [c for c in today if str(c["timestamp"])[:10] == day]
    allc, _ = market_data.get_ema_warmup_candles(key, "5minute", today, min_candles=30)
    atr_c, _ = market_data.get_atr_warmup_candles(key, "5minute", today, min_candles=15)
    atr = compute_atr(atr_c)
    if not atr or atr <= 0:
        return []
    sig = rfp.detect(symbol, today, allc, atr)
    if not sig:
        return []
    s = _build_setup(symbol, sig, atr, session)
    return [s] if s else []


def detect_all_rfp_setups() -> list:
    session = current_session()
    if session is None:
        return []
    found = []
    for symbol in rfp.NSE_SYMBOLS:
        try:
            found.extend(_scan_symbol(symbol, session))
        except Exception as exc:
            _logger.error("RFP_SYMBOL_FAILED %s: %s: %s", symbol, type(exc).__name__, exc, exc_info=True)
    return found
