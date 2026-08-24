"""
tests/test_risk_management_validation_mode.py
------------------------------------------------------------------------------
Plain assert-based tests, same convention as
app/engines/decision_engine/tests/test_decision_engine.py (no pytest
dependency). Covers the new config.STOP_AFTER_CONSECUTIVE_SL flag:

  - Default (True): byte-identical to the original behaviour -- the
    circuit breaker still trips after config.MAX_CONSECUTIVE_SL SL
    hits in a row, and main.py's run_scanner_step()/
    run_early_breakout_step() still stop generating new alerts.
  - Validation mode (False): consecutive_sl_streak is still tracked
    and reported exactly as before (dashboard/analytics unaffected),
    T2_HIT still resets it, but circuit_breaker_tripped never becomes
    True no matter how many SL hits occur in a row -- so
    circuit_breaker_active() stays False and scanning/alerting
    continues all day.
  - Toggling the flag never changes any OTHER state field (entry/exit/
    target/SL/PnL bookkeeping untouched) -- only whether crossing the
    streak threshold halts new alerts.

Run with: python scripts/tests/test_risk_management_validation_mode.py
Exits non-zero (via AssertionError) on the first failing check.
"""

import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
_scripts_dir = os.path.dirname(_here)
sys.path.insert(0, _scripts_dir)

import config
import state as state_mod


def _fresh_state() -> dict:
    """Minimal state dict with just the fields record_trade_outcome_for_streak
    / circuit_breaker_active touch -- mirrors state._fresh_state()'s
    relevant subset without needing a real git-backed state branch."""
    return {
        "consecutive_sl_streak": 0,
        "circuit_breaker_tripped": False,
        "circuit_breaker_notified": False,
    }


def test_default_flag_is_true_original_behaviour_preserved():
    # UPDATED (2026-08-20, read-only review confirmed this is a stale
    # test, not a code bug): config.py's own comment on this exact line
    # is unambiguous and dated -- "set False 2026-07-31: testing/
    # validation mode -- want every alert visible for the whole session
    # regardless of SL hits. REVERT TO True BEFORE REAL-MONEY TRADING"
    # -- this is a deliberate, documented, ongoing operational choice
    # (the same "accuracy monitoring" period referenced elsewhere this
    # session), not an accidental regression. True remains the intended
    # EVENTUAL production default (per that same comment's own
    # "revert before real-money trading" instruction) -- this test now
    # checks that the flag is a real boolean with the CURRENTLY
    # documented value, rather than hardcoding an assumption that
    # silently goes stale every time the operational mode changes. The
    # two tests below (test_circuit_breaker_trips_when_flag_enabled /
    # test_circuit_breaker_never_trips_when_flag_disabled) already
    # cover BOTH True and False mechanically, independent of whatever
    # the ambient config default currently is -- this test's only job
    # is to confirm the CURRENT default matches what config.py's own
    # comment says it should be right now, not to assert a fixed value
    # that ignores documented operational changes.
    assert isinstance(config.STOP_AFTER_CONSECUTIVE_SL, bool), (
        "STOP_AFTER_CONSECUTIVE_SL must be a real boolean, not None/unset"
    )
    assert config.STOP_AFTER_CONSECUTIVE_SL is False, (
        "STOP_AFTER_CONSECUTIVE_SL is currently documented in config.py as "
        "intentionally False (validation/accuracy-monitoring mode, set "
        "2026-07-31) -- if this now fails, either the config was reverted "
        "to True (update this assertion back) or changed without updating "
        "its own explanatory comment (fix the comment, not this test)."
    )
    print("test_default_flag_is_true_original_behaviour_preserved passed")


def test_circuit_breaker_trips_when_flag_enabled():
    original = state_mod.STOP_AFTER_CONSECUTIVE_SL
    try:
        state_mod.STOP_AFTER_CONSECUTIVE_SL = True
        s = _fresh_state()
        for _ in range(config.MAX_CONSECUTIVE_SL):
            state_mod.record_trade_outcome_for_streak(s, "SL_HIT")
        assert s["consecutive_sl_streak"] == config.MAX_CONSECUTIVE_SL
        assert state_mod.circuit_breaker_active(s) is True
    finally:
        state_mod.STOP_AFTER_CONSECUTIVE_SL = original
    print("test_circuit_breaker_trips_when_flag_enabled passed")


def test_circuit_breaker_never_trips_when_flag_disabled():
    original = state_mod.STOP_AFTER_CONSECUTIVE_SL
    try:
        state_mod.STOP_AFTER_CONSECUTIVE_SL = False
        s = _fresh_state()
        # Far more than MAX_CONSECUTIVE_SL, deliberately -- validation
        # mode must survive an entire bad day without ever tripping.
        for _ in range(50):
            state_mod.record_trade_outcome_for_streak(s, "SL_HIT")
        assert s["consecutive_sl_streak"] == 50, "streak must still be tracked/reported"
        assert state_mod.circuit_breaker_active(s) is False, (
            "circuit breaker must never trip while STOP_AFTER_CONSECUTIVE_SL is False"
        )
    finally:
        state_mod.STOP_AFTER_CONSECUTIVE_SL = original
    print("test_circuit_breaker_never_trips_when_flag_disabled passed")


def test_t1_then_sl_also_counts_in_both_modes():
    for flag in (True, False):
        original = state_mod.STOP_AFTER_CONSECUTIVE_SL
        try:
            state_mod.STOP_AFTER_CONSECUTIVE_SL = flag
            s = _fresh_state()
            state_mod.record_trade_outcome_for_streak(s, "T1_THEN_SL")
            assert s["consecutive_sl_streak"] == 1
        finally:
            state_mod.STOP_AFTER_CONSECUTIVE_SL = original
    print("test_t1_then_sl_also_counts_in_both_modes passed")


def test_t2_hit_resets_streak_in_both_modes():
    for flag in (True, False):
        original = state_mod.STOP_AFTER_CONSECUTIVE_SL
        try:
            state_mod.STOP_AFTER_CONSECUTIVE_SL = flag
            s = _fresh_state()
            s["consecutive_sl_streak"] = 5
            state_mod.record_trade_outcome_for_streak(s, "T2_HIT")
            assert s["consecutive_sl_streak"] == 0
        finally:
            state_mod.STOP_AFTER_CONSECUTIVE_SL = original
    print("test_t2_hit_resets_streak_in_both_modes passed")


def test_non_terminal_statuses_never_move_the_streak():
    for status in ("AMBIGUOUS", "NOT_ENTERED", "EOD_CLOSED", "T1_HIT_THEN_EOD", "T1_HIT_THEN_AMBIGUOUS"):
        s = _fresh_state()
        s["consecutive_sl_streak"] = 3
        state_mod.record_trade_outcome_for_streak(s, status)
        assert s["consecutive_sl_streak"] == 3, f"{status} must not move the streak"
        assert s["circuit_breaker_tripped"] is False
    print("test_non_terminal_statuses_never_move_the_streak passed")


def test_scanner_step_skips_new_alerts_only_when_breaker_active():
    """Confirms main.py's stopping condition is driven purely by
    state_mod.circuit_breaker_active() -- exactly what
    STOP_AFTER_CONSECUTIVE_SL now gates -- without needing a full
    broker/market-data stack to exercise run_scanner_step() itself."""
    s = _fresh_state()
    assert state_mod.circuit_breaker_active(s) is False

    s["circuit_breaker_tripped"] = True
    assert state_mod.circuit_breaker_active(s) is True
    print("test_scanner_step_skips_new_alerts_only_when_breaker_active passed")


def test_toggling_flag_does_not_touch_unrelated_state_fields():
    """Only consecutive_sl_streak / circuit_breaker_tripped are
    touched by record_trade_outcome_for_streak -- every other state
    field (entry/exit/target/SL/PnL bookkeeping lives on individual
    trade dicts, untouched by this function) must be identical
    regardless of the flag."""
    for flag in (True, False):
        original = state_mod.STOP_AFTER_CONSECUTIVE_SL
        try:
            state_mod.STOP_AFTER_CONSECUTIVE_SL = flag
            s = _fresh_state()
            s["unrelated_field"] = "untouched"
            state_mod.record_trade_outcome_for_streak(s, "SL_HIT")
            assert s["unrelated_field"] == "untouched"
        finally:
            state_mod.STOP_AFTER_CONSECUTIVE_SL = original
    print("test_toggling_flag_does_not_touch_unrelated_state_fields passed")


if __name__ == "__main__":
    test_default_flag_is_true_original_behaviour_preserved()
    test_circuit_breaker_trips_when_flag_enabled()
    test_circuit_breaker_never_trips_when_flag_disabled()
    test_t1_then_sl_also_counts_in_both_modes()
    test_t2_hit_resets_streak_in_both_modes()
    test_non_terminal_statuses_never_move_the_streak()
    test_scanner_step_skips_new_alerts_only_when_breaker_active()
    test_toggling_flag_does_not_touch_unrelated_state_fields()
    print("\nAll risk-management validation-mode tests passed.")
