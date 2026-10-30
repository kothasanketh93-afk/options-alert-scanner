"""
app/engines/fib_pivot/tests/test_pivot_calc.py -- unit tests for the
classic Fibonacci pivot point formula.
------------------------------------------------------------------------------
Plain assert-based, no pytest dependency -- same convention as
app/engines/early_breakout/tests/test_engine.py (this repo doesn't use
pytest; see that file's own docstring). All synthetic, deterministic
inputs -- no broker/network access.

Run with: python scripts/app/engines/fib_pivot/tests/test_pivot_calc.py
Exits non-zero (via AssertionError) on the first failing check.
"""

import sys
import os

_here = os.path.dirname(os.path.abspath(__file__))
_scripts_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_here))))
sys.path.insert(0, _scripts_dir)

from app.engines.fib_pivot import pivot_calc


def test_compute_fibonacci_pivots_known_values():
    # H=25000, L=24800, C=24900 -> P=24900 exactly, range=200, so every
    # ratio lands on a clean, hand-checkable number.
    pivots = pivot_calc.compute_fibonacci_pivots(25000, 24800, 24900)
    assert pivots["P"] == 24900.0, pivots
    assert pivots["R1"] == 24976.4, pivots   # 24900 + 0.382*200
    assert pivots["R2"] == 25023.6, pivots   # 24900 + 0.618*200
    assert pivots["R3"] == 25100.0, pivots   # 24900 + 1.000*200
    assert pivots["S1"] == 24823.6, pivots   # 24900 - 0.382*200
    assert pivots["S2"] == 24776.4, pivots   # 24900 - 0.618*200
    assert pivots["S3"] == 24700.0, pivots   # 24900 - 1.000*200
    # Monotonic ordering must always hold regardless of the actual numbers.
    assert pivots["S3"] < pivots["S2"] < pivots["S1"] < pivots["P"] < pivots["R1"] < pivots["R2"] < pivots["R3"]


def test_compute_fibonacci_pivots_rejects_bad_input():
    try:
        pivot_calc.compute_fibonacci_pivots(24800, 25000, 24900)  # high < low
        assert False, "expected ValueError for high < low"
    except ValueError:
        pass

    try:
        pivot_calc.compute_fibonacci_pivots(None, 24800, 24900)
        assert False, "expected ValueError for None input"
    except ValueError:
        pass


def test_next_resistance_and_support_beyond():
    pivots = pivot_calc.compute_fibonacci_pivots(25000, 24800, 24900)
    assert pivot_calc.next_resistance_beyond(pivots, "P") == pivots["R1"]
    assert pivot_calc.next_resistance_beyond(pivots, "R1") == pivots["R2"]
    assert pivot_calc.next_resistance_beyond(pivots, "R2") == pivots["R3"]
    assert pivot_calc.next_resistance_beyond(pivots, "R3") is None  # already the highest tracked level

    assert pivot_calc.next_support_beyond(pivots, "P") == pivots["S1"]
    assert pivot_calc.next_support_beyond(pivots, "S1") == pivots["S2"]
    assert pivot_calc.next_support_beyond(pivots, "S2") == pivots["S3"]
    assert pivot_calc.next_support_beyond(pivots, "S3") is None

    assert pivot_calc.next_resistance_beyond(pivots, "not_a_level") is None
    assert pivot_calc.next_support_beyond(pivots, "not_a_level") is None


if __name__ == "__main__":
    test_compute_fibonacci_pivots_known_values()
    test_compute_fibonacci_pivots_rejects_bad_input()
    test_next_resistance_and_support_beyond()
    print("All pivot_calc tests passed.")
