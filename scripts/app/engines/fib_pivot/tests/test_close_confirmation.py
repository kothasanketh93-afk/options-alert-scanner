"""
app/engines/fib_pivot/tests/test_close_confirmation.py -- unit tests for
the "candle must CLOSE beyond the level, on a FRESH cross" entry trigger.
------------------------------------------------------------------------------
Plain assert-based, no pytest dependency -- same convention as
app/engines/early_breakout/tests/test_engine.py.

Run with: python scripts/app/engines/fib_pivot/tests/test_close_confirmation.py
"""

import sys
import os

_here = os.path.dirname(os.path.abspath(__file__))
_scripts_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_here))))
sys.path.insert(0, _scripts_dir)

from app.engines.fib_pivot import pivot_calc, close_confirmation


def _candles(*closes):
    """Minimal synthetic candles -- only "close" matters to this module;
    open/high/low are filled in with the close so the dicts are still
    shape-complete for any caller that reads them."""
    return [{"open": c, "high": c, "low": c, "close": c} for c in closes]


PIVOTS = pivot_calc.compute_fibonacci_pivots(25000, 24800, 24900)  # R1=24976.4, S1=24823.6


def test_ce_fresh_cross_above_r1_confirms():
    candles = _candles(24970.0, 24980.0)  # prev below R1, current above R1
    result = close_confirmation.detect_close_confirmation(candles, PIVOTS, "CE")
    assert result["confirmed"] is True, result
    assert result["level_name"] == "R1", result


def test_pe_fresh_cross_below_s1_confirms():
    candles = _candles(24830.0, 24820.0)  # prev above S1, current below S1
    result = close_confirmation.detect_close_confirmation(candles, PIVOTS, "PE")
    assert result["confirmed"] is True, result
    assert result["level_name"] == "S1", result


def test_already_extended_move_does_not_confirm():
    """
    This is the specific anti-chasing behavior this module exists for
    (see its own module docstring): if the PRIOR candle was already
    closed above R1, a later candle still being above R1 is NOT a fresh
    cross and must not confirm, even though "close > R1" is still true.
    """
    candles = _candles(24990.0, 25000.0)  # prev ALREADY above R1 -> not fresh
    result = close_confirmation.detect_close_confirmation(candles, PIVOTS, "CE")
    assert result["confirmed"] is False, result


def test_no_cross_at_all_does_not_confirm():
    candles = _candles(24850.0, 24860.0)  # both closes sit between S1 and R1
    result = close_confirmation.detect_close_confirmation(candles, PIVOTS, "CE")
    assert result["confirmed"] is False, result
    result = close_confirmation.detect_close_confirmation(candles, PIVOTS, "PE")
    assert result["confirmed"] is False, result


def test_nearest_level_wins_on_a_double_cross_candle():
    """A gap candle that jumps straight past BOTH R1 and R2 on CLOSE
    should report R1 (the nearer, first-crossed level), not R2."""
    candles = _candles(24970.0, 25050.0)  # prev below R1, current above R2
    result = close_confirmation.detect_close_confirmation(candles, PIVOTS, "CE")
    assert result["confirmed"] is True, result
    assert result["level_name"] == "R1", result


def test_insufficient_candles_does_not_confirm():
    assert close_confirmation.detect_close_confirmation([], PIVOTS, "CE") == {"confirmed": False}
    assert close_confirmation.detect_close_confirmation(_candles(24970.0), PIVOTS, "CE") == {"confirmed": False}


def test_unknown_direction_does_not_confirm():
    candles = _candles(24970.0, 24980.0)
    assert close_confirmation.detect_close_confirmation(candles, PIVOTS, "XX") == {"confirmed": False}


if __name__ == "__main__":
    test_ce_fresh_cross_above_r1_confirms()
    test_pe_fresh_cross_below_s1_confirms()
    test_already_extended_move_does_not_confirm()
    test_no_cross_at_all_does_not_confirm()
    test_nearest_level_wins_on_a_double_cross_candle()
    test_insufficient_candles_does_not_confirm()
    test_unknown_direction_does_not_confirm()
    print("All close_confirmation tests passed.")
