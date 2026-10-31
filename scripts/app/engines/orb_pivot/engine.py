"""
ORB15 + pivot-break shadow engine entry point. Mirrors fib_pivot/engine.py:
underlying checks first, option leg looked up only once a signal exists.
main.create_orb_pivot_shadow_candidates() is the only caller.
"""
import market_data
from logging_setup import get_logger
import config
from indicators import compute_atr
from option_chain import resolve_underlying_key, get_atm_strike, current_expiry, _extract_leg
from filters import liquidity_ok
from levels import dynamic_trade_levels, estimated_holding_minutes
from scanner import current_session

from . import signals

_logger = get_logger("orb_pivot")


def _build_setup(strategy, symbol, signal, atr, target_mult, session, wide_prev_day=None):
    direction = signal["direction"]
    underlying_key = resolve_underlying_key(symbol)
    atm_strike, spot_price = get_atm_strike(symbol)
    expiry = current_expiry(symbol)
    chain = market_data.get_option_chain(underlying_key, expiry)
    leg = _extract_leg(chain, atm_strike, direction)
    if leg is None:
        return None
    premium, delta = leg["ltp"], leg["delta"]
    if premium is None or delta is None or delta == 0:
        return None
    if not liquidity_ok(leg["oi"], leg["volume"], leg["bid"], leg["ask"], premium, symbol):
        _logger.info("ORBPIVOT_REJECTED %s %s %s: liquidity gate failed", strategy, symbol, direction)
        return None

    zone = dynamic_trade_levels(premium, atr, delta, direction, spot_price, None)
    lv = signals.option_levels(premium, delta, atr, target_mult)
    rr = (lv["target_1"] - premium) / (premium - lv["stop_loss"]) if premium > lv["stop_loss"] else 0.0
    reasons = {
        "setup": f"{strategy}: 5m close {'above' if direction == 'CE' else 'below'} "
                 f"{signal['level_name']} ({signal['level_value']})",
        "risk_reward": f"1:{rr:.1f}",
        "liquidity": "Passing the hard filter",
    }
    why = [f"✓ {v}" for v in reasons.values()]
    holding = estimated_holding_minutes(atr, target_mult * atr)
    return {
        "strategy": strategy,
        "symbol": symbol, "direction": direction, "moneyness": "ATM",
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
        "expected_holding_minutes": holding, "why_reasons": why,
        "session": session, "alternatives": [],
        "pivot_level_name": signal["level_name"], "pivot_level_value": signal["level_value"],
        "atr": round(atr, 2),
        "wide_prev_day": wide_prev_day,
        "entry_oi": leg.get("oi"), "entry_volume": leg.get("volume"),
        "spread_pct": (round((leg["ask"] - leg["bid"]) / premium * 100, 2)
                       if leg.get("bid") and leg.get("ask") else None),
    }


def _scan_symbol(symbol, session):
    underlying_key = resolve_underlying_key(symbol)
    if not underlying_key:
        return []
    daily = market_data.get_recent_daily_candles(underlying_key, 12)
    if not daily:
        return []
    prev = daily[-1]
    wide = signals.is_wide_prev_day(daily)
    today = market_data.get_recent_underlying_candles(
        underlying_key, config.ORB_PIVOT_CANDLE_INTERVAL, config.ORB_PIVOT_CANDLE_FETCH_COUNT)
    if len(today) < signals.OR_CANDLES + 1:
        return []
    atr_candles, _ = market_data.get_atr_warmup_candles(
        underlying_key, config.ORB_PIVOT_CANDLE_INTERVAL, today, min_candles=15)
    atr = compute_atr(atr_candles)
    if atr is None or atr <= 0:
        return []

    out = []
    orb = signals.detect_orb15(today, prev["close"])
    if orb:
        s = _build_setup("orb15", symbol, orb, atr, config.ORB15_TARGET_ATR_MULT, session, wide)
        if s:
            out.append(s)
    pb = signals.detect_pivot_break(today, prev["high"], prev["low"], prev["close"])
    if pb:
        s = _build_setup("pivot_break", symbol, pb, atr, config.PIVOT_BREAK_TARGET_ATR_MULT, session, wide)
        if s:
            out.append(s)
    pdhl = signals.detect_pdhl_retest(today, prev["high"], prev["low"])
    if pdhl:
        s = _build_setup("pdhl_retest", symbol, pdhl, atr, config.PDHL_TARGET_ATR_MULT, session, wide)
        if s:
            out.append(s)
    return out


def detect_all_orb_pivot_setups() -> list:
    session = current_session()
    if session is None:
        return []
    found = []
    for symbol in config.ORB_PIVOT_SYMBOLS:
        try:
            found.extend(_scan_symbol(symbol, session))
        except Exception as exc:
            _logger.error("ORBPIVOT_SYMBOL_FAILED %s: %s: %s", symbol, type(exc).__name__, exc, exc_info=True)
    return found
