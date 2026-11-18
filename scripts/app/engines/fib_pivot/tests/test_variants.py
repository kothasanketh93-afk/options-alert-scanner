"""Tests for the Fib pivot experiment variants (retest detector, ITM1 strike pick, variant config)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))))

import config
from app.engines.fib_pivot import close_confirmation as cc, engine, pivot_calc

PIV = pivot_calc.compute_fibonacci_pivots(25000, 24800, 24900)
R1 = PIV["R1"]


def c(o, h, l, cl):
    return {"open": o, "high": h, "low": l, "close": cl}


def test_retest_ce_detected():
    candles = [c(R1 - 30, R1 - 5, R1 - 40, R1 - 10), c(R1 - 10, R1 + 25, R1 - 12, R1 + 20),
               c(R1 + 20, R1 + 30, R1 + 10, R1 + 25), c(R1 + 25, R1 + 28, R1 + 2, R1 + 12)]
    out = cc.detect_retest(candles, PIV, "CE", atr=20.0)
    assert out["confirmed"] and out["level_name"] == "R1"


def test_retest_requires_prior_breakout():
    candles = [c(R1 + 40, R1 + 50, R1 + 30, R1 + 45)] * 4 + [c(R1 + 40, R1 + 45, R1 + 1, R1 + 10)]
    assert not cc.detect_retest(candles, PIV, "CE", atr=20.0)["confirmed"]


def test_retest_rejects_close_below_level():
    candles = [c(R1 - 30, R1 - 5, R1 - 40, R1 - 10), c(R1 - 10, R1 + 25, R1 - 12, R1 + 20),
               c(R1 + 20, R1 + 30, R1 + 10, R1 + 25), c(R1 + 25, R1 + 28, R1 - 5, R1 - 2)]
    assert not cc.detect_retest(candles, PIV, "CE", atr=20.0)["confirmed"]


def test_itm1_strike_picks_one_step_inside():
    chain = [{"strike_price": k} for k in (24900, 24950, 25000, 25050)]
    assert engine._itm1_strike(chain, 25000, "CE") == 24950
    assert engine._itm1_strike(chain, 25000, "PE") == 25050
    assert engine._itm1_strike(chain, 24900, "CE") is None


def test_variants_have_unique_names_and_change_one_thing():
    names = [v["name"] for v in config.FIB_PIVOT_VARIANTS]
    assert len(names) == len(set(names)) and "fib_pivot" not in names
    for v in config.FIB_PIVOT_VARIANTS:
        assert len([k for k in v if k not in ("name", "fetch_count")]) == 1
