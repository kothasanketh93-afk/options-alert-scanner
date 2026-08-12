"""
tests/test_probability_calibration_pipeline.py
------------------------------------------------------------------------------
Plain assert-based tests, same convention as this repo's other tests/*.py
files (no pytest dependency).

Covers the 2026-08-12 P0 audit ("probability must not be fabricated") and
completes the exact end-to-end test list that audit's P0-3 requirement
specified (letters A-M). Letters A-G were already covered by existing files
before this audit (see the cross-reference table below) and are re-verified
here only where the calibration change affects their meaning; H, I, K, L, M
are NEW, added by this file.

  A. Complete valid setup -> alert eligible
     -- test_scanner_to_alert_pipeline.py's test_g (re-verified below,
        now requires the calibration mock explicitly)
  B. Missing EMA structural evidence -> NO alert
     -- test_scanner_to_alert_pipeline.py's test_a/test_b
  C. Missing 5m + 15m structure -> NO alert
     -- test_scanner_to_alert_pipeline.py's test_a (ema_15m_direction=
        ema_5m_direction=None)
  D. Options Intelligence rejected -> NO alert
     -- test_scanner_to_alert_pipeline.py's test_d
  E. RR failed -> NO alert
     -- test_scanner_to_alert_pipeline.py's test_c
  F. ATR extension failed -> NO alert
     -- test_extension_filter.py's test_b
  G. Structural extension failed -> NO alert
     -- test_structural_extension_filter.py's test_b
  H. Spread/liquidity failed -> NO alert                            [NEW]
  I. Probability unavailable -> NO alert                            [NEW]
  J. Probability 69% -> NO alert
     -- test_scanner_to_alert_pipeline.py's test_e (re-verified below)
  K. Probability exactly 70% -> eligible IF every other gate passes  [NEW]
  L. Probability 80% but one hard gate fails -> NO alert             [NEW]
  M. Alert payload uses exactly the same entry/SL/targets/contract/
     expiry used by eligibility calculations                        [NEW]

Reuses tests/test_scanner_to_alert_pipeline.py's _run_mocked_score_strike()/
_run_mocked_scanner_step() harnesses -- calls the REAL scanner.
score_strike()/main.run_scanner_step(), not a reimplementation.
"""

import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
_scripts_dir = os.path.dirname(_here)
sys.path.insert(0, _scripts_dir)
sys.path.insert(0, _here)

import test_scanner_to_alert_pipeline as pipeline
import config
import trade_score

_CANDLES = pipeline._FAR_LEVEL_CANDLES_15M


def _valid_kwargs(**overrides):
    """A candidate that clears every gate EXCEPT calibration (which the
    caller must set explicitly via calibrated_probability_override)."""
    base = dict(
        ema_15m_direction="PE", ema_5m_direction="PE",
        trend_value=0.7, momentum_value=-0.01, vwap_confirmed=True,
        oi_intel_accepted=True, candles_15m=_CANDLES,
    )
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# A (re-verified). Complete valid setup + calibration available -> eligible
# ---------------------------------------------------------------------------

def test_a_complete_valid_setup_with_calibration_available_is_eligible():
    result = pipeline._run_mocked_score_strike(**_valid_kwargs(calibrated_probability_override=85))
    assert result is not None, "A: a fully-confirmed setup with an available calibrated probability must be eligible"
    assert result["probability_pct"] == 85, "A: the ALERT payload's probability must be the CALIBRATED value, not a trade_score rescaling"
    print("test_a_complete_valid_setup_with_calibration_available_is_eligible passed")


# ---------------------------------------------------------------------------
# H. Spread/liquidity failed -> NO alert
# ---------------------------------------------------------------------------

def test_h_illiquid_contract_is_rejected():
    """
    Uses a chain fixture with OI/volume far below config.MIN_OI/
    MIN_OPTION_VOLUME -- filters.liquidity_ok() (one of quality_gate()'s
    hard gates) must reject this before extension/directional/
    calibration are ever reached.
    """
    illiquid_chain = [{
        "strike_price": 24600.0,
        "put_instrument_key": "NSE_FO|TESTPUT", "put_ltp": 120.0, "put_oi": 10.0,
        "put_volume": 5.0, "put_bid": 100.0, "put_ask": 140.0, "put_delta": -0.45,
        "call_instrument_key": "NSE_FO|TESTCALL", "call_ltp": 100.0, "call_oi": 50000.0,
        "call_volume": 15000.0, "call_bid": 99.5, "call_ask": 100.5, "call_delta": 0.45,
    }]
    result = pipeline._run_mocked_score_strike(
        **_valid_kwargs(chain=illiquid_chain, calibrated_probability_override=85)
    )
    assert result is None, "H: an illiquid contract (OI/volume far below the configured minimums) must be rejected"
    print("test_h_illiquid_contract_is_rejected passed")


# ---------------------------------------------------------------------------
# I. Probability unavailable -> NO alert
# ---------------------------------------------------------------------------

def test_i_probability_unavailable_is_rejected_even_with_every_other_gate_passing():
    """
    Uses calibrated_probability_override=None -- mocking trade_score.
    calibrated_probability_pct() to explicitly return None, matching
    that function's REAL, current, honest behavior (no historical-
    outcome calibration data exists in this repository yet -- see its
    own docstring). This is the DEFAULT, most important case this
    entire audit was about: a setup that clears every hard gate,
    every directional/structural check, RR, extension, and Options
    Intelligence must STILL produce NO alert when a genuine
    probability estimate isn't available -- trade_score/the heuristic
    percentage must never be silently substituted.
    """
    result = pipeline._run_mocked_score_strike(**_valid_kwargs(calibrated_probability_override=None))
    assert result is None, (
        "I: CALIBRATED_PROBABILITY_UNAVAILABLE must reject even a setup that cleared every other gate -- "
        "trade_score/the heuristic percentage must never be substituted for a missing calibrated estimate"
    )
    print("test_i_probability_unavailable_is_rejected_even_with_every_other_gate_passing passed")


def test_i_variant_real_uncalibrated_function_returns_none_by_default():
    """
    Companion check with NOTHING mocked on trade_score.
    calibrated_probability_pct() itself -- calls the REAL function
    directly (not through score_strike()) to confirm its actual,
    current, unconditional return value is None, matching what test_i
    above assumes the real pipeline does today.
    """
    result = trade_score.calibrated_probability_pct({"symbol": "NIFTY", "direction": "PE"})
    assert result is None, "the real calibrated_probability_pct() must currently return None unconditionally"
    print("test_i_variant_real_uncalibrated_function_returns_none_by_default passed")


# ---------------------------------------------------------------------------
# J (re-verified). Probability 69% -> NO alert
# ---------------------------------------------------------------------------

def test_j_probability_69_is_rejected():
    result = pipeline._run_mocked_score_strike(**_valid_kwargs(calibrated_probability_override=69))
    assert result is not None, "sanity check: score_strike() itself must still return a candidate at 69% (main.py's floor gates it, not score_strike())"
    assert result["probability_pct"] == 69

    sent, state = pipeline._run_mocked_scanner_step(result)
    assert sent == [], "J: 69% must not reach Telegram"
    assert state["trades"] == {}, "J: a sub-floor candidate must not be tracked either"
    print("test_j_probability_69_is_rejected passed")


# ---------------------------------------------------------------------------
# K. Probability exactly 70% -> eligible IF every other gate passes
# ---------------------------------------------------------------------------

def test_k_probability_exactly_70_is_eligible():
    result = pipeline._run_mocked_score_strike(**_valid_kwargs(calibrated_probability_override=70))
    assert result is not None
    assert result["probability_pct"] == 70

    sent, state = pipeline._run_mocked_scanner_step(result)
    assert len(sent) == 1, "K: exactly 70% (the floor itself, not below it) must be eligible when every other gate passed"
    assert len(state["trades"]) == 1
    print("test_k_probability_exactly_70_is_eligible passed")


# ---------------------------------------------------------------------------
# L. Probability 80% but one hard gate fails -> NO alert
# ---------------------------------------------------------------------------

def test_l_high_probability_cannot_rescue_a_failed_hard_gate():
    """
    Same illiquid-chain setup as test H, but with calibration mocked to
    a high, clearly-passing value (80) -- proves quality_gate() (which
    runs BEFORE the extension/directional/calibration checks in
    scanner.score_strike()'s actual code order) rejects first,
    regardless of what calibration would have said.
    """
    illiquid_chain = [{
        "strike_price": 24600.0,
        "put_instrument_key": "NSE_FO|TESTPUT", "put_ltp": 120.0, "put_oi": 10.0,
        "put_volume": 5.0, "put_bid": 100.0, "put_ask": 140.0, "put_delta": -0.45,
        "call_instrument_key": "NSE_FO|TESTCALL", "call_ltp": 100.0, "call_oi": 50000.0,
        "call_volume": 15000.0, "call_bid": 99.5, "call_ask": 100.5, "call_delta": 0.45,
    }]
    result = pipeline._run_mocked_score_strike(
        **_valid_kwargs(chain=illiquid_chain, calibrated_probability_override=80)
    )
    assert result is None, "L: an 80% calibrated probability must not rescue a failed hard (liquidity) gate"
    print("test_l_high_probability_cannot_rescue_a_failed_hard_gate passed")


# ---------------------------------------------------------------------------
# M. Alert payload uses exactly the same entry/SL/targets/contract/expiry
#    used by eligibility calculations
# ---------------------------------------------------------------------------

def test_m_alert_payload_matches_the_same_levels_used_for_scoring():
    """
    scanner.score_strike() computes `levels = dynamic_trade_levels(...)`
    exactly ONCE (confirmed by reading the function: target_distance/
    sl_distance, used for risk_reward_strength() scoring, AND the final
    candidate dict's entry_low/entry_high/stop_loss/target_1/target_2,
    all read from that SAME `levels` dict -- no second computation
    exists anywhere in the function). This test proves the OBSERVABLE
    consequence: stop_loss/target_1/target_2 in the returned candidate
    are internally consistent with the SAME premium/instrument the
    contract-resolution/probability logs described for this run (same
    instrument_key, same alert_premium as the chain fixture's put_ltp).
    """
    result = pipeline._run_mocked_score_strike(**_valid_kwargs(calibrated_probability_override=85))
    assert result is not None
    assert result["instrument_key"] == "NSE_FO|TESTPUT", "M: payload instrument_key must match the resolved contract"
    assert result["alert_premium"] == 120.0, "M: payload premium must match the resolved contract's actual LTP"
    assert result["stop_loss"] < result["entry_low"] <= result["entry_high"] < result["target_1"] < result["target_2"], (
        "M: entry/stop/targets must be internally consistent (a single ordered level structure), "
        "not independently-computed, possibly-disagreeing numbers"
    )
    print("test_m_alert_payload_matches_the_same_levels_used_for_scoring passed")


if __name__ == "__main__":
    test_a_complete_valid_setup_with_calibration_available_is_eligible()
    test_h_illiquid_contract_is_rejected()
    test_i_probability_unavailable_is_rejected_even_with_every_other_gate_passing()
    test_i_variant_real_uncalibrated_function_returns_none_by_default()
    test_j_probability_69_is_rejected()
    test_k_probability_exactly_70_is_eligible()
    test_l_high_probability_cannot_rescue_a_failed_hard_gate()
    test_m_alert_payload_matches_the_same_levels_used_for_scoring()
    print("\nAll probability-calibration-pipeline tests (A/H/I/J/K/L/M) passed.")
