"""Liquidity-sweep / sweep+FVG shadow engine entry point (index options only). main.create_sweep_shadow_candidates() is the caller."""
import market_data
import config
from logging_setup import get_logger
from indicators import compute_atr
from option_chain import resolve_underlying_key, get_atm_strike, current_expiry, _extract_leg
from filters import liquidity_ok
from levels import estimated_holding_minutes
from scanner import current_session

from . import signals

_logger = get_logger("sweep_reversal")


def _build_setup(strategy, symbol, signal, atr, session, wide_prev_day=None):
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
        _logger.info("SWEEP_REJECTED %s %s %s: liquidity gate failed", strategy, symbol, direction)
        return None
    lv = signals.option_levels(premium, delta, signal["signal_close"], signal["extreme"], atr, direction)
    rr = (lv["target_1"] - premium) / (premium - lv["stop_loss"]) if premium > lv["stop_loss"] else 0.0
    fvg = signal.get("fvg")
    reasons = {
        "setup": f"{strategy}: swept {signal['level_name']} ({signal['level_value']}), reclaimed; "
                 f"extreme {signal['extreme']}" + (f"; FVG {fvg[0]}-{fvg[1]} retested" if fvg else ""),
        "risk_reward": f"1:{rr:.1f}",
        "liquidity": "Passing the hard filter",
    }
    why = [f"✓ {v}" for v in reasons.values()]
    return {
        "strategy": strategy, "symbol": symbol, "direction": direction, "moneyness": "ATM",
        "instrument_key": leg["instrument_key"], "strike": atm_strike, "expiry": expiry,
        "spot_price": spot_price, "alert_premium": premium,
        "entry_low": premium, "entry_high": premium,
        "stop_loss": lv["stop_loss"], "target_1": lv["target_1"], "target_2": lv["target_2"],
        "grade": "Shadow", "trade_score": 50, "probability_pct": None, "probability_label": "Shadow",
        "score_breakdown": [f"{k.title()}: {v}" for k, v in reasons.items()],
        "confidence_score": 5.0, "risk_score": round(min(10.0, rr * 3), 1),
        "reward_score": round(min(10.0, rr * 4), 1), "expected_rr": f"1:{rr:.1f}",
        "factor_checklist": why, "confidence_stars": "☆☆☆☆☆", "confidence_label": "Shadow",
        "risk_rating": "🟡", "risk_label": "Unvalidated",
        "expected_holding_minutes": estimated_holding_minutes(atr, lv["risk_underlying"] * signals.T1_R),
        "why_reasons": why, "session": session, "alternatives": [],
        "pivot_level_name": signal["level_name"], "pivot_level_value": signal["level_value"],
        "sweep_extreme": signal["extreme"], "fvg": list(fvg) if fvg else None,
        "atr": round(atr, 2), "wide_prev_day": wide_prev_day,
        "entry_oi": leg.get("oi"), "entry_volume": leg.get("volume"),
        "spread_pct": (round((leg["ask"] - leg["bid"]) / premium * 100, 2)
                       if leg.get("bid") and leg.get("ask") else None),
    }


def _scan_symbol(symbol, session, state=None, now_mins=None):
    underlying_key = resolve_underlying_key(symbol)
    if not underlying_key:
        return []
    daily = market_data.get_recent_daily_candles(underlying_key, 12)
    if not daily:
        return []
    prev = daily[-1]
    today = market_data.get_recent_underlying_candles(
        underlying_key, config.SWEEP_CANDLE_INTERVAL, config.SWEEP_CANDLE_FETCH_COUNT)
    if len(today) < 4:
        return []
    atr_candles, _ = market_data.get_atr_warmup_candles(underlying_key, config.SWEEP_CANDLE_INTERVAL, today, min_candles=15)
    atr = compute_atr(atr_candles)
    if atr is None or atr <= 0:
        return []
    out = []
    if state is not None and now_mins is not None:
        try:
            fs = _flow_setup(symbol, session, state, now_mins, atr)
            if fs:
                out.append(fs)
        except Exception as exc:
            _logger.error("OIFLOW_FAILED %s: %s: %s", symbol, type(exc).__name__, exc)
    for name, fn in (("sweep", signals.detect_sweep), ("sweep_fvg", signals.detect_sweep_fvg),
                     ("amd", signals.detect_amd)):
        sig = fn(today, prev["high"], prev["low"])
        if sig:
            s = _build_setup(name, symbol, sig, atr, session)
            if s:
                out.append(s)
    return out


def _flow_snapshot(symbol, underlying_key, atr):
    """ATM +/- 3 strikes call/put OI total + spot, for the order-flow proxy. None if the chain is unavailable."""
    atm_strike, spot = get_atm_strike(symbol)
    chain = market_data.get_option_chain(underlying_key, current_expiry(symbol))
    strikes = sorted({r["strike_price"] for r in chain if r.get("strike_price") is not None})
    if atm_strike not in strikes:
        return None
    i = strikes.index(atm_strike)
    near = set(strikes[max(0, i - 3): i + 4])
    c = sum((r.get("call_oi") or 0) for r in chain if r.get("strike_price") in near)
    p = sum((r.get("put_oi") or 0) for r in chain if r.get("strike_price") in near)
    if not (c or p):
        return None
    return {"spot": spot, "call_oi": c, "put_oi": p}


def _flow_setup(symbol, session, state, now_mins, atr):
    underlying_key = resolve_underlying_key(symbol)
    snap = _flow_snapshot(symbol, underlying_key, atr)
    if snap is None:
        return None
    snap["t"] = now_mins
    hist = state.setdefault("flow_snap", {}).setdefault(symbol, [])
    hist.append(snap)
    del hist[:-8]
    sig = signals.detect_oi_flow(hist, atr)
    return _build_setup("oi_flow", symbol, sig, atr, session) if sig else None


def detect_all_sweep_setups(state: dict = None, now_mins: int = None) -> list:
    session = current_session()
    if session is None:
        return []
    found = []
    for symbol in config.SWEEP_SYMBOLS:
        try:
            found.extend(_scan_symbol(symbol, session, state, now_mins))
        except Exception as exc:
            _logger.error("SWEEP_SYMBOL_FAILED %s: %s: %s", symbol, type(exc).__name__, exc, exc_info=True)
    return found
