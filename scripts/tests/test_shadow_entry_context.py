"""
Tests for main._shadow_entry_context() -- diagnostic-only log suffix for
SHADOW_ENTRY_ACTIVATED / SHADOW_OUTCOME_SL_FIRST (added 2026-10-31).
Run with: python scripts/tests/test_shadow_entry_context.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main


def test_full_trade_produces_all_fields():
    trade = {
        "alert_premium": 100.0, "tracked_entry_price": 110.0,
        "alert_time": "2026-10-30T10:50:00+05:30",
        "actual_entry_time": "2026-10-30T10:53:00+05:30",
        "grade": "B", "trade_score": 62, "session": "10:35-11:35",
        "max_favorable_pct": 1.5, "max_adverse_pct": -8.0,
    }
    out = main._shadow_entry_context(trade)
    assert "chase_pct=10.0" in out
    assert "alert_to_entry_min=3.0" in out
    assert "grade=B" in out and "score=62" in out
    assert "session=10:35-11:35" in out
    assert "mfe_pct=1.5" in out and "mae_pct=-8.0" in out
    assert out.startswith(" ")


def test_empty_trade_returns_empty_string():
    assert main._shadow_entry_context({}) == ""


def test_bad_values_never_raise():
    assert main._shadow_entry_context({"alert_time": "garbage", "actual_entry_time": "x",
                                       "alert_premium": 1, "tracked_entry_price": 2}) == ""


def test_function_does_not_mutate_trade():
    trade = {"alert_premium": 100.0, "tracked_entry_price": 90.0, "grade": "B"}
    before = dict(trade)
    main._shadow_entry_context(trade)
    assert trade == before


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("PASS:", name)
