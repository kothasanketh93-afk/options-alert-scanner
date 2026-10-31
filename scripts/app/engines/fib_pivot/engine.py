"""
app/engines/fib_pivot/engine.py -- Fibonacci Pivot Shadow Engine entry point.
------------------------------------------------------------------------------
SHADOW-ONLY (see app/engines/fib_pivot/__init__.py's module docstring
for why). Produces trade dicts with the same keys main.new_trade_record()
already expects -- same reuse pattern app/engines/early_breakout/engine.py
documents at its own top -- so tracker.process_trade_candles() works on these records with
zero code duplication. main.create_fib_pivot_shadow_candidates() is the
only caller; it stores results in state["fib_pivot_shadow_trades"],
never state["trades"] or state["shadow_trades"].

Entry rule, per symbol+direction, every scan cycle:
  1. 9-EMA trend filter on the underlying's own intraday candles
     (config.FIB_PIVOT_CANDLE_INTERVAL, default "15minute"): CE requires
     the latest closed candle's close ABOVE EMA9, PE requires it BELOW.
  2. Daily Fibonacci pivot levels (pivot_calc.py) from the prior trading
     day's daily H/L/C.
  3. close_confirmation.py: the latest closed candle must be a FRESH
     close-beyond-level cross (not an old, already-extended one) in the
     matching direction.
  4. Only once 1-3 all pass is an option leg even looked up (same
     "underlying first, option last" ordering early_breakout/engine.py
     uses, for the same reason: never pay for an option-chain call for a
     setup that was going to be rejected on the underlying alone).
  5. Same liquidity hard-filter (filters.liquidity_ok) and minimum R:R
     floor (config.FIB_PIVOT_MIN_RR) as every other engine in this repo
     -- these are data-quality/sizing floors, not something specific to
     this strategy, so there is no reason to relax them here.
"""

import market_data
from logging_setup import get_logger
from config import (
    SYMBOLS, FIB_PIVOT_CANDLE_INTERVAL, FIB_PIVOT_CANDLE_FETCH_COUNT,
    FIB_PIVOT_EMA_PERIOD, FIB_PIVOT_MIN_RR,
)
from indicators import compute_ema, compute_atr
from option_chain import resolve_underlying_key, get_atm_strike, current_expiry, _extract_leg
from filters import liquidity_ok
from levels import dynamic_trade_levels, estimated_holding_minutes
from scanner import current_session

from . import pivot_calc, close_confirmation

_logger = get_logger("fib_pivot")

_last_scan_stats = {"candidates": 0, "rejected": 0}


def _score(rr_ratio: float, candles_since_cross: int = 0) -> int:
    """
    Deliberately simple, provisional 0-100 score -- NOT a calibrated
    win-probability (this strategy has zero track record in this
    codebase yet, unlike trade_score.py's weights, which were tuned
    against a dataset; see this engine's own module docstring). Its
    only purpose is to rank same-cycle candidates for the daily/shadow
    summary and log lines; it must never be read as "X% likely to win"
    anywhere downstream, and nothing in main.py wires it to a Telegram
    alert threshold the way EARLY_BREAKOUT_TELEGRAM_MIN_SCORE does.
    """
    rr_component = max(0.0, min(1.0, (rr_ratio - FIB_PIVOT_MIN_RR) / FIB_PIVOT_MIN_RR))
    return round(50 + rr_component * 50)


def _score_symbol_direction(symbol: str, direction: str, session: str):
    underlying_key = resolve_underlying_key(symbol)
    if not underlying_key:
        return None

    daily_candles = market_data.get_recent_daily_candles(underlying_key, 3)
    if not daily_candles:
        return None
    prev_day = daily_candles[-1]
    pivots = pivot_calc.compute_fibonacci_pivots(prev_day["high"], prev_day["low"], prev_day["close"])

    today_candles = market_data.get_recent_underlying_candles(
        underlying_key, FIB_PIVOT_CANDLE_INTERVAL, FIB_PIVOT_CANDLE_FETCH_COUNT
    )
    candles, _ = market_data.get_atr_warmup_candles(
        underlying_key, FIB_PIVOT_CANDLE_INTERVAL, today_candles, min_candles=FIB_PIVOT_CANDLE_FETCH_COUNT
    )
    # Need enough for EMA9, ATR(14), and >=2 candles for the close-
    # confirmation cross check -- ATR(14) (indicators.compute_atr's
    # default period) is the binding constraint at period+1=15.
    if len(candles) < max(FIB_PIVOT_EMA_PERIOD, 14) + 2:
        return None

    closes = [c["close"] for c in candles]
    ema9 = compute_ema(closes, FIB_PIVOT_EMA_PERIOD)
    atr = compute_atr(candles)
    if ema9 is None or atr is None or atr <= 0:
        return None

    last_close = candles[-1]["close"]
    if direction == "CE" and not (last_close > ema9):
        return None
    if direction == "PE" and not (last_close < ema9):
        return None

    confirmation = close_confirmation.detect_close_confirmation(candles, pivots, direction)
    if not confirmation["confirmed"]:
        return None

    # --- Option selected ONLY NOW, after the underlying-side checks all confirm ---
    atm_strike, spot_price = get_atm_strike(symbol)
    expiry = current_expiry(symbol)
    chain = market_data.get_option_chain(underlying_key, expiry)
    leg = _extract_leg(chain, atm_strike, direction)
    if leg is None:
        return None

    instrument_key = leg["instrument_key"]
    premium, oi, volume = leg["ltp"], leg["oi"], leg["volume"]
    bid, ask, delta = leg["bid"], leg["ask"], leg["delta"]
    if premium is None or delta is None or delta == 0:
        return None

    if not liquidity_ok(oi, volume, bid, ask, premium, symbol):
        _last_scan_stats["rejected"] += 1
        _logger.info("FIBPIVOT_REJECTED %s %s: liquidity gate failed", symbol, direction)
        return None

    # The next pivot level beyond the one just crossed is a genuine
    # structural level still AHEAD of price (unlike early_breakout/
    # engine.py's base-window high/low, which is already-broken
    # structure by the time of a breakout -- see that file's own
    # comment) -- use it as dynamic_trade_levels()'s target cap.
    if direction == "CE":
        nearby_level = pivot_calc.next_resistance_beyond(pivots, confirmation["level_name"])
    else:
        nearby_level = pivot_calc.next_support_beyond(pivots, confirmation["level_name"])

    levels = dynamic_trade_levels(premium, atr, delta, direction, spot_price, nearby_level)
    target_distance = levels["target_1"] - premium
    sl_distance = premium - levels["stop_loss"]
    rr_ratio = (target_distance / sl_distance) if sl_distance > 0 else 0.0
    if rr_ratio < FIB_PIVOT_MIN_RR:
        _last_scan_stats["rejected"] += 1
        _logger.info("FIBPIVOT_REJECTED %s %s: RR %.2f below minimum %.2f",
                      symbol, direction, rr_ratio, FIB_PIVOT_MIN_RR)
        return None

    score = _score(rr_ratio)
    holding_minutes = estimated_holding_minutes(atr, abs(target_distance / delta) if delta else 0)

    reasons = {
        "trend": f"Underlying close {'above' if direction == 'CE' else 'below'} EMA{FIB_PIVOT_EMA_PERIOD}",
        "pivot": (
            f"Fresh close {'above' if direction == 'CE' else 'below'} "
            f"{confirmation['level_name']} ({confirmation['level_value']}) -- "
            f"prior candle close {confirmation['prev_close']}, this candle close {confirmation['close']}"
        ),
        "risk_reward": f"1:{rr_ratio:.1f}",
        "liquidity": "Passing the hard filter",
    }
    why_reasons = [f"✓ {v}" for v in reasons.values()]

    return {
        "strategy": "fib_pivot",
        "symbol": symbol, "direction": direction, "moneyness": "ATM",
        "instrument_key": instrument_key, "strike": atm_strike, "expiry": expiry,
        "spot_price": spot_price, "alert_premium": premium,
        "entry_low": levels["entry_low"], "entry_high": levels["entry_high"],
        "stop_loss": levels["stop_loss"], "target_1": levels["target_1"], "target_2": levels["target_2"],
        "grade": "Shadow", "trade_score": score,
        "probability_pct": None,  # deliberately unscored -- see this engine's module docstring
        "probability_label": "Shadow",
        "score_breakdown": [f"{k.title()}: {v}" for k, v in reasons.items()],
        "confidence_score": round(score / 10, 1),
        "risk_score": round(min(10.0, rr_ratio * 3), 1),
        "reward_score": round(min(10.0, rr_ratio * 4), 1),
        "expected_rr": f"1:{rr_ratio:.1f}",
        "factor_checklist": why_reasons,
        "confidence_stars": "☆☆☆☆☆",  # unscored strategy -- no confidence claim, see probability_pct above
        "confidence_label": "Shadow",
        "risk_rating": "🟡",
        "risk_label": "Unvalidated",
        "expected_holding_minutes": holding_minutes,
        "why_reasons": why_reasons,
        "session": session, "alternatives": [],
        "fib_pivot_reasons": reasons,
        "pivot_level_name": confirmation["level_name"],
        "pivot_level_value": confirmation["level_value"],
        "pivots": pivots,
        "ema9": round(ema9, 2),
        "atr": round(atr, 2),
    }


def detect_all_fib_pivot_setups() -> list:
    """
    Top-level entry point, called once per scanner cycle from
    main.create_fib_pivot_shadow_candidates(). Returns setups sorted
    best-first (by the provisional score, see _score()'s own caveat).
    """
    session = current_session()
    if session is None:
        return []

    _last_scan_stats["candidates"] = 0
    _last_scan_stats["rejected"] = 0

    candidates = []
    for symbol in SYMBOLS:
        for direction in ("CE", "PE"):
            try:
                setup = _score_symbol_direction(symbol, direction, session)
            except Exception as exc:
                _logger.error(
                    "FIBPIVOT_SYMBOL_FAILED %s %s: %s: %s",
                    symbol, direction, type(exc).__name__, exc, exc_info=True,
                )
                continue
            if setup:
                candidates.append(setup)

    candidates.sort(key=lambda c: c["trade_score"], reverse=True)
    _last_scan_stats["candidates"] = len(candidates)
    return candidates


def get_last_scan_stats() -> dict:
    return dict(_last_scan_stats)
