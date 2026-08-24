"""
tests/test_shadow_mode.py
------------------------------------------------------------------------------
Plain assert-based tests, same convention as this repo's other tests/*.py
files (no pytest dependency).

Covers the 2026-08-23 Phase-2 Shadow/Validation Mode feature
(config.PROBABILITY_SHADOW_MODE_ENABLED, scanner.py's shadow-candidate
buffer, main.py's create_shadow_candidates()/run_shadow_tracker_step()/
maybe_send_shadow_summary()).

Reuses tests/test_scanner_to_alert_pipeline.py's _run_mocked_score_strike()
harness -- calls the REAL scanner.score_strike(), not a reimplementation --
and tracker.process_trade_candles() directly for outcome determinism,
matching the same "test the real function" convention this repo already
uses throughout.

Required cases (verbatim from the approved scope):
  1. shadow mode OFF -> no calibrated probability -> no candidate alert
     (and no shadow candidate captured either)
  2. shadow mode ON -> qualifying candidate is recorded
  3. shadow candidate never sends Telegram live alert
  4. all hard gates remain mandatory
  5. candidate gets deterministic outcome
  6. duplicate candidate is not recorded twice
"""

import os
import sys
from datetime import datetime, timezone, timedelta
from unittest import mock

_here = os.path.dirname(os.path.abspath(__file__))
_scripts_dir = os.path.dirname(_here)
sys.path.insert(0, _scripts_dir)
sys.path.insert(0, _here)

import test_scanner_to_alert_pipeline as pipeline
import test_probability_calibration_pipeline as calib_pipeline
import scanner
import main
import tracker
from exceptions import TelegramFailure

IST = timezone(timedelta(hours=5, minutes=30))
_CANDLES = pipeline._FAR_LEVEL_CANDLES_15M


class _Patch:
    def __init__(self, obj, attr, value):
        self.obj, self.attr, self.value = obj, attr, value
        self._had = hasattr(obj, attr)
        self._orig = getattr(obj, attr, None)

    def __enter__(self):
        setattr(self.obj, self.attr, self.value)
        return self.value

    def __exit__(self, *exc):
        if self._had:
            setattr(self.obj, self.attr, self._orig)
        else:
            delattr(self.obj, self.attr)


def _valid_kwargs(**overrides):
    return calib_pipeline._valid_kwargs(**overrides)


# ---------------------------------------------------------------------------
# 1. shadow mode OFF -> no calibrated probability -> no candidate alert,
#    and no shadow candidate captured either
# ---------------------------------------------------------------------------

def test_1_shadow_off_produces_no_live_candidate_and_no_shadow_candidate():
    scanner.reset_shadow_candidates()
    with _Patch(scanner, "PROBABILITY_SHADOW_MODE_ENABLED", False):
        result = pipeline._run_mocked_score_strike(**_valid_kwargs(calibrated_probability_override=None))
    assert result is None, "1: live pipeline must still reject when calibration is unavailable"
    assert scanner.get_shadow_candidates() == [], "1: shadow mode OFF must capture nothing, even when the candidate otherwise qualifies"
    print("test_1_shadow_off_produces_no_live_candidate_and_no_shadow_candidate passed")


# ---------------------------------------------------------------------------
# 2. shadow mode ON -> qualifying candidate is recorded
# ---------------------------------------------------------------------------

def test_2_shadow_on_qualifying_candidate_is_captured():
    scanner.reset_shadow_candidates()
    with _Patch(scanner, "PROBABILITY_SHADOW_MODE_ENABLED", True):
        result = pipeline._run_mocked_score_strike(**_valid_kwargs(calibrated_probability_override=None))
        shadow_candidates = scanner.get_shadow_candidates()
    assert result is None, "2: LIVE pipeline result must be unchanged (still None) -- shadow mode never alters it"
    assert len(shadow_candidates) == 1, f"2: exactly one shadow candidate must be captured, got {len(shadow_candidates)}"
    candidate = shadow_candidates[0]
    assert candidate["execution_mode"] == "SHADOW"
    assert candidate["probability_status"] == "UNAVAILABLE"
    assert candidate["probability_pct"] is None, "2: probability_pct must be None, never heuristic_pct"
    assert candidate["live_alert_sent"] is False
    assert "candidate_id" in candidate and candidate["candidate_id"]
    print("test_2_shadow_on_qualifying_candidate_is_captured passed")


# ---------------------------------------------------------------------------
# 3. shadow candidate never sends Telegram live alert
# ---------------------------------------------------------------------------

def test_3_shadow_candidate_never_sends_telegram():
    scanner.reset_shadow_candidates()
    with _Patch(scanner, "PROBABILITY_SHADOW_MODE_ENABLED", True):
        pipeline._run_mocked_score_strike(**_valid_kwargs(calibrated_probability_override=None))
        shadow_candidates = scanner.get_shadow_candidates()

    now = datetime(2026, 8, 23, 9, 30, 0, tzinfo=IST)
    state = {"date": "2026-08-23", "trades": {}, "shadow_trades": {}}

    with _Patch(scanner, "get_shadow_candidates", lambda: shadow_candidates), \
         _Patch(main.config, "PROBABILITY_SHADOW_MODE_ENABLED", True), \
         _Patch(main.tg, "send", mock.Mock(side_effect=AssertionError("tg.send must NEVER be called for a shadow candidate"))):
        main.create_shadow_candidates(state, now)

    assert len(state["shadow_trades"]) == 1, "3: candidate must still be recorded even with Telegram forbidden"
    shadow_trade = list(state["shadow_trades"].values())[0]
    assert shadow_trade["execution_mode"] == "SHADOW"
    assert shadow_trade["live_alert_sent"] is False

    # Now drive it through an outcome (T1 hit) and confirm STILL no Telegram call.
    entry = shadow_trade["entry_low"]
    t1 = shadow_trade["target_1"]
    fake_candles = [
        {"open": entry, "high": entry + 0.5, "low": entry - 0.5, "close": entry, "timestamp": "2026-08-23T09:31:00+05:30"},
        {"open": entry, "high": t1 + 1, "low": entry, "close": t1 + 1, "timestamp": "2026-08-23T09:32:00+05:30"},
    ]
    with _Patch(main.market_data, "get_1min_candles_since", lambda *a, **k: fake_candles), \
         _Patch(main.config, "PROBABILITY_SHADOW_MODE_ENABLED", True), \
         _Patch(main.tg, "send", mock.Mock(side_effect=AssertionError("tg.send must NEVER be called for a shadow outcome either"))):
        main.run_shadow_tracker_step(state, now)

    # If we reach here without the AssertionError firing, Telegram was never called -- confirmed.
    print("test_3_shadow_candidate_never_sends_telegram passed")


# ---------------------------------------------------------------------------
# 4. all hard gates remain mandatory
# ---------------------------------------------------------------------------

def test_4_hard_gates_remain_mandatory_even_in_shadow_mode():
    """An RR-failing candidate must be rejected by the RR gate LONG
    before it ever reaches the calibration gate -- shadow mode must
    never bypass, weaken, or skip any existing hard gate. Reuses the
    same near-level default candle fixture test_scanner_to_alert_
    pipeline.py's own test_c already relies on for this (NOT the
    _FAR_LEVEL_CANDLES_15M _valid_kwargs() otherwise defaults to)."""
    scanner.reset_shadow_candidates()
    with _Patch(scanner, "PROBABILITY_SHADOW_MODE_ENABLED", True):
        result = pipeline._run_mocked_score_strike(
            ema_15m_direction="PE", ema_5m_direction="PE",
            trend_value=0.6, momentum_value=0.01, vwap_confirmed=True,
            oi_intel_accepted=True,
            calibrated_probability_override=None,
        )
        shadow_candidates = scanner.get_shadow_candidates()
    assert result is None, "4: RR gate must still reject"
    assert shadow_candidates == [], "4: a candidate that failed an EARLIER hard gate must never reach the shadow-capture point at all"
    print("test_4_hard_gates_remain_mandatory_even_in_shadow_mode passed")


# ---------------------------------------------------------------------------
# 5. candidate gets deterministic outcome
# ---------------------------------------------------------------------------

def test_5_shadow_candidate_gets_deterministic_outcome():
    """Directly exercises tracker.process_trade_candles() (REUSED,
    unmodified) on a shadow-shaped trade dict -- same mechanism the
    live path already relies on, proving outcome determination is
    identical regardless of execution_mode."""
    trade = {
        "symbol": "NIFTY", "direction": "PE", "moneyness": "ATM",
        "instrument_key": "TEST|X", "strike": 24600.0, "expiry": "2026-08-27",
        "entry_low": 100.0, "entry_high": 102.0, "stop_loss": 90.0,
        "target_1": 115.0, "target_2": 130.0,
        "status": "WAITING_ENTRY", "final_status": None,
        "actual_entry_time": None, "tracked_entry_price": None,
        "highest_since_entry": None, "lowest_since_entry": None,
        "max_favorable_pct": 0.0, "max_adverse_pct": 0.0,
        "t1_hit": False, "t1_hit_time": None, "t2_hit": False, "t2_hit_time": None,
        "sl_hit_time": None, "eod_last_price": None,
        "last_processed_ts": "2026-08-23T09:30:00+05:30",
        "execution_mode": "SHADOW", "probability_status": "UNAVAILABLE",
        "live_alert_sent": False, "candidate_id": "NIFTY:PE:24600.0:2026-08-27",
    }
    candles = [
        {"open": 99.0, "high": 101.0, "low": 98.0, "close": 100.5, "timestamp": "2026-08-23T09:31:00+05:30"},   # entry
        {"open": 100.5, "high": 116.0, "low": 100.0, "close": 116.0, "timestamp": "2026-08-23T09:35:00+05:30"},  # T1
    ]
    events = tracker.process_trade_candles(trade, candles)
    assert trade["t1_hit"] is True
    assert any(e["type"] == "T1_HIT" for e in events), f"5: T1_HIT event expected, got {[e['type'] for e in events]}"
    print("test_5_shadow_candidate_gets_deterministic_outcome passed")


# ---------------------------------------------------------------------------
# 6. duplicate candidate is not recorded twice
# ---------------------------------------------------------------------------

def test_6_duplicate_candidate_not_recorded_twice():
    scanner.reset_shadow_candidates()
    with _Patch(scanner, "PROBABILITY_SHADOW_MODE_ENABLED", True):
        pipeline._run_mocked_score_strike(**_valid_kwargs(calibrated_probability_override=None))
        shadow_candidates = scanner.get_shadow_candidates()
    assert len(shadow_candidates) == 1

    now = datetime(2026, 8, 23, 9, 30, 0, tzinfo=IST)
    state = {"date": "2026-08-23", "trades": {}, "shadow_trades": {}}

    with _Patch(scanner, "get_shadow_candidates", lambda: shadow_candidates), \
         _Patch(main.config, "PROBABILITY_SHADOW_MODE_ENABLED", True):
        main.create_shadow_candidates(state, now)  # cycle 1
        assert len(state["shadow_trades"]) == 1

        # Same candidate_id reappears on the NEXT 5-minute cycle (the
        # setup is still active, not yet terminal) -- must NOT create
        # a second entry.
        main.create_shadow_candidates(state, now)  # cycle 2, same candidate

    assert len(state["shadow_trades"]) == 1, (
        f"6: duplicate candidate_id must not create a second shadow trade while the first "
        f"is still active, got {len(state['shadow_trades'])} entries"
    )
    print("test_6_duplicate_candidate_not_recorded_twice passed")


def test_6_variant_terminal_candidate_allows_a_fresh_recreation():
    """Companion check: once the FIRST shadow trade for a candidate_id
    has resolved to a terminal status, a later reappearance of the same
    setup shape IS allowed to create a new shadow trade -- this isn't a
    duplicate, it's a new occurrence."""
    scanner.reset_shadow_candidates()
    with _Patch(scanner, "PROBABILITY_SHADOW_MODE_ENABLED", True):
        pipeline._run_mocked_score_strike(**_valid_kwargs(calibrated_probability_override=None))
        shadow_candidates = scanner.get_shadow_candidates()
    now = datetime(2026, 8, 23, 9, 30, 0, tzinfo=IST)
    state = {"date": "2026-08-23", "trades": {}, "shadow_trades": {}}

    with _Patch(scanner, "get_shadow_candidates", lambda: shadow_candidates), \
         _Patch(main.config, "PROBABILITY_SHADOW_MODE_ENABLED", True):
        main.create_shadow_candidates(state, now)
        first_id = list(state["shadow_trades"].keys())[0]
        state["shadow_trades"][first_id]["status"] = "SL_HIT"  # mark terminal

        main.create_shadow_candidates(state, now)  # same candidate_id again

    assert len(state["shadow_trades"]) == 2, "6-variant: a NEW shadow trade is allowed once the prior one is terminal"
    print("test_6_variant_terminal_candidate_allows_a_fresh_recreation passed")


if __name__ == "__main__":
    test_1_shadow_off_produces_no_live_candidate_and_no_shadow_candidate()
    test_2_shadow_on_qualifying_candidate_is_captured()
    test_3_shadow_candidate_never_sends_telegram()
    test_4_hard_gates_remain_mandatory_even_in_shadow_mode()
    test_5_shadow_candidate_gets_deterministic_outcome()
    test_6_duplicate_candidate_not_recorded_twice()
    test_6_variant_terminal_candidate_allows_a_fresh_recreation()
    print("\nAll shadow-mode tests (1-6) passed.")
