"""
app/engines/fib_pivot/tests/test_engine.py -- integration-style tests for
_score_symbol_direction()/detect_all_fib_pivot_setups(), with every
broker/market-data call monkeypatched (no real network, no real auth --
same posture as every other test file in this repo).

Monkeypatches the NAMES BOUND INSIDE app.engines.fib_pivot.engine's own
module namespace (it does `from market_data import ...`-style imports),
not the original modules -- the standard approach for testing
"from X import Y" bindings, and restored in a try/finally per test so
tests don't leak state into each other.

Plain assert-based, no pytest dependency -- same convention as
app/engines/early_breakout/tests/test_engine.py.

Run with: python scripts/app/engines/fib_pivot/tests/test_engine.py
"""

import sys
import os

_here = os.path.dirname(os.path.abspath(__file__))
_scripts_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_here))))
sys.path.insert(0, _scripts_dir)

import market_data  # noqa: F401  (imported so engine.py's `import market_data` patches affect this same module object)
from app.engines.fib_pivot import engine


# --- Synthetic data shared by every test below ---

DAILY_CANDLES = [{"open": 24850, "high": 25000, "low": 24800, "close": 24900}]
# R1 = 24976.4 (same numbers test_close_confirmation.py already exercises).


def _uptrend_candles():
    """
    20 candles, gently rising, ATR(14)/EMA9-safe (plenty of warmup),
    ending with a FRESH close-above-R1 cross on the last candle (prev
    close 24970.0 <= R1, current close 24980.0 > R1) -- a clean CE
    confirmation. Mirrors test_close_confirmation.py's own fresh-cross
    fixture so the two test files agree on what "confirmed" means.
    """
    closes = [24700 + i * 15 for i in range(18)]  # 24700 .. 24955
    closes += [24970.0, 24980.0]
    candles = []
    for c in closes:
        candles.append({"open": c - 2, "high": c + 5, "low": c - 5, "close": float(c), "volume": 1000})
    return candles


def _downtrend_candles():
    """Mirror of _uptrend_candles() for the PE side -- ends with a fresh
    close-below-S1 cross (S1 = 24823.6)."""
    closes = [25100 - i * 15 for i in range(18)]  # 25100 .. 24845
    closes += [24830.0, 24820.0]
    candles = []
    for c in closes:
        candles.append({"open": c + 2, "high": c + 5, "low": c - 5, "close": float(c), "volume": 1000})
    return candles


GOOD_LEG = {
    "instrument_key": "NSE_FO|TEST12345",
    "ltp": 120.0, "oi": 50000, "volume": 10000,
    "bid": 119.5, "ask": 120.5, "delta": 0.35,
}


class _Patcher:
    """Tiny context manager: sets attrs on `engine`, restores the
    original values on exit -- avoids hand-rolling try/finally at every
    call site below."""

    def __init__(self, **attrs):
        self._attrs = attrs
        self._originals = {}

    def __enter__(self):
        for name, value in self._attrs.items():
            self._originals[name] = getattr(engine, name)
            setattr(engine, name, value)
        return self

    def __exit__(self, *exc_info):
        for name, value in self._originals.items():
            setattr(engine, name, value)
        return False


def _patch_happy_path(trend_candles, direction):
    return _Patcher(
        resolve_underlying_key=lambda symbol: "NSE_INDEX|TEST",
        current_session=lambda: "morning",
        FIB_PIVOT_MIN_RR=0.1,  # see module docstring: not exercising ATR/RR sizing math here, just the wiring
        get_atm_strike=lambda symbol: (24900.0, 24900.0),
        current_expiry=lambda symbol: "2026-11-05",
        _extract_leg=lambda chain, strike, direction_: dict(GOOD_LEG),
        liquidity_ok=lambda oi, volume, bid, ask, premium, symbol=None: True,
    )


def test_ce_setup_detected_on_fresh_cross_with_bullish_ema():
    candles = _uptrend_candles()
    orig_daily = market_data.get_recent_daily_candles
    orig_recent = market_data.get_recent_underlying_candles
    orig_warmup = market_data.get_atr_warmup_candles
    orig_chain = market_data.get_option_chain
    try:
        market_data.get_recent_daily_candles = lambda key, count: DAILY_CANDLES
        market_data.get_recent_underlying_candles = lambda key, interval, count: candles
        market_data.get_atr_warmup_candles = lambda key, interval, today, min_candles: (candles, False)
        market_data.get_option_chain = lambda key, expiry: ["fake-chain"]

        with _patch_happy_path(candles, "CE"):
            setup = engine._score_symbol_direction("NIFTY", "CE", "morning")
    finally:
        market_data.get_recent_daily_candles = orig_daily
        market_data.get_recent_underlying_candles = orig_recent
        market_data.get_atr_warmup_candles = orig_warmup
        market_data.get_option_chain = orig_chain

    assert setup is not None, "expected a confirmed CE setup"
    assert setup["strategy"] == "fib_pivot"
    assert setup["direction"] == "CE"
    assert setup["pivot_level_name"] == "R1"
    assert setup["probability_pct"] is None  # deliberately unscored -- see engine.py's module docstring
    assert setup["grade"] == "Shadow"
    assert 0 <= setup["trade_score"] <= 100
    # new_trade_record()'s required keys must all be present (main.py reads
    # these without .get() defaults for several of them).
    for required_key in (
        "symbol", "direction", "moneyness", "instrument_key", "strike", "expiry",
        "spot_price", "alert_premium", "entry_low", "entry_high", "stop_loss",
        "target_1", "target_2", "session", "grade", "trade_score", "probability_pct",
        "confidence_score", "risk_score", "reward_score", "expected_rr",
        "factor_checklist", "confidence_stars", "confidence_label", "risk_rating",
        "risk_label", "expected_holding_minutes", "why_reasons", "alternatives",
    ):
        assert required_key in setup, f"missing required key: {required_key}"


def test_pe_setup_detected_on_fresh_cross_with_bearish_ema():
    candles = _downtrend_candles()
    orig_daily = market_data.get_recent_daily_candles
    orig_recent = market_data.get_recent_underlying_candles
    orig_warmup = market_data.get_atr_warmup_candles
    orig_chain = market_data.get_option_chain
    try:
        market_data.get_recent_daily_candles = lambda key, count: DAILY_CANDLES
        market_data.get_recent_underlying_candles = lambda key, interval, count: candles
        market_data.get_atr_warmup_candles = lambda key, interval, today, min_candles: (candles, False)
        market_data.get_option_chain = lambda key, expiry: ["fake-chain"]

        with _patch_happy_path(candles, "PE"):
            setup = engine._score_symbol_direction("NIFTY", "PE", "morning")
    finally:
        market_data.get_recent_daily_candles = orig_daily
        market_data.get_recent_underlying_candles = orig_recent
        market_data.get_atr_warmup_candles = orig_warmup
        market_data.get_option_chain = orig_chain

    assert setup is not None, "expected a confirmed PE setup"
    assert setup["direction"] == "PE"
    assert setup["pivot_level_name"] == "S1"


def test_no_setup_when_ema_trend_disagrees_with_direction():
    """Uptrend candles (bullish EMA9, fresh CROSS above R1 on close) must
    NOT produce a PE setup -- the EMA9 trend filter has to actually gate,
    not just be computed and ignored."""
    candles = _uptrend_candles()
    orig_daily = market_data.get_recent_daily_candles
    orig_recent = market_data.get_recent_underlying_candles
    orig_warmup = market_data.get_atr_warmup_candles
    orig_chain = market_data.get_option_chain
    try:
        market_data.get_recent_daily_candles = lambda key, count: DAILY_CANDLES
        market_data.get_recent_underlying_candles = lambda key, interval, count: candles
        market_data.get_atr_warmup_candles = lambda key, interval, today, min_candles: (candles, False)
        market_data.get_option_chain = lambda key, expiry: ["fake-chain"]

        with _patch_happy_path(candles, "PE"):
            setup = engine._score_symbol_direction("NIFTY", "PE", "morning")
    finally:
        market_data.get_recent_daily_candles = orig_daily
        market_data.get_recent_underlying_candles = orig_recent
        market_data.get_atr_warmup_candles = orig_warmup
        market_data.get_option_chain = orig_chain

    assert setup is None


def test_no_setup_when_liquidity_filter_fails():
    candles = _uptrend_candles()
    orig_daily = market_data.get_recent_daily_candles
    orig_recent = market_data.get_recent_underlying_candles
    orig_warmup = market_data.get_atr_warmup_candles
    orig_chain = market_data.get_option_chain
    try:
        market_data.get_recent_daily_candles = lambda key, count: DAILY_CANDLES
        market_data.get_recent_underlying_candles = lambda key, interval, count: candles
        market_data.get_atr_warmup_candles = lambda key, interval, today, min_candles: (candles, False)
        market_data.get_option_chain = lambda key, expiry: ["fake-chain"]

        with _Patcher(
            resolve_underlying_key=lambda symbol: "NSE_INDEX|TEST",
            current_session=lambda: "morning",
            FIB_PIVOT_MIN_RR=0.1,
            get_atm_strike=lambda symbol: (24900.0, 24900.0),
            current_expiry=lambda symbol: "2026-11-05",
            _extract_leg=lambda chain, strike, direction_: dict(GOOD_LEG),
            liquidity_ok=lambda oi, volume, bid, ask, premium, symbol=None: False,  # hard fail
        ):
            setup = engine._score_symbol_direction("NIFTY", "CE", "morning")
    finally:
        market_data.get_recent_daily_candles = orig_daily
        market_data.get_recent_underlying_candles = orig_recent
        market_data.get_atr_warmup_candles = orig_warmup
        market_data.get_option_chain = orig_chain

    assert setup is None


def test_no_setup_when_insufficient_candle_history():
    orig_daily = market_data.get_recent_daily_candles
    orig_recent = market_data.get_recent_underlying_candles
    orig_warmup = market_data.get_atr_warmup_candles
    try:
        too_few = _uptrend_candles()[-5:]  # fewer than max(EMA_PERIOD, 14) + 2
        market_data.get_recent_daily_candles = lambda key, count: DAILY_CANDLES
        market_data.get_recent_underlying_candles = lambda key, interval, count: too_few
        market_data.get_atr_warmup_candles = lambda key, interval, today, min_candles: (too_few, False)

        with _Patcher(resolve_underlying_key=lambda symbol: "NSE_INDEX|TEST", current_session=lambda: "morning"):
            setup = engine._score_symbol_direction("NIFTY", "CE", "morning")
    finally:
        market_data.get_recent_daily_candles = orig_daily
        market_data.get_recent_underlying_candles = orig_recent
        market_data.get_atr_warmup_candles = orig_warmup

    assert setup is None


def test_no_setup_when_underlying_unresolvable():
    with _Patcher(resolve_underlying_key=lambda symbol: None):
        setup = engine._score_symbol_direction("NOT_A_REAL_SYMBOL", "CE", "morning")
    assert setup is None


if __name__ == "__main__":
    test_ce_setup_detected_on_fresh_cross_with_bullish_ema()
    test_pe_setup_detected_on_fresh_cross_with_bearish_ema()
    test_no_setup_when_ema_trend_disagrees_with_direction()
    test_no_setup_when_liquidity_filter_fails()
    test_no_setup_when_insufficient_candle_history()
    test_no_setup_when_underlying_unresolvable()
    print("All fib_pivot engine tests passed.")
