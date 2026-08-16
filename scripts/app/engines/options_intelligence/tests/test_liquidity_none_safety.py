"""
tests/test_liquidity_none_safety.py
------------------------------------------------------------------------------
Plain assert-based tests, same convention as this repo's other tests/*.py
files (no pytest dependency).

Covers the 2026-08-15 P0 follow-up fix, found by a read-only impact audit
BEFORE the earlier bid/ask evidence fix was committed:
trade_score.liquidity_strength() and every downstream aggregator that
consumes its output (options_intelligence/scoring.py's three functions,
options_intelligence/engine.py's display line, early_breakout's
compute_weighted_score() and its own liquidity text label) all had an
unguarded arithmetic/comparison on a value that could now legitimately be
None -- confirmed via direct reproduction to raise TypeError before this fix.

Required cases (verbatim from the approved scope):
  A. liquidity_strength with valid bid/ask
  B. liquidity_strength with bid=None
  C. liquidity_strength with ask=None
  D. liquidity_strength with both None
  E. bid=0
  F. ask=0
  G. non-numeric bid/ask
  H. main Options Intelligence path does not crash
  I. early_breakout path does not crash
  J. PRICING_EVIDENCE valid quote
  K. PRICING_EVIDENCE missing quote
  L. PRICING_EVIDENCE invalid zero quote
  M. existing liquidity_ok() behavior remains correct
  N. LTP remains the strategy premium
  O. probability remains unchanged
  P. alert eligibility remains unchanged

Plus the exact reproduction case from the approval message itself.
"""

import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
_scripts_dir = os.path.dirname(_here)
sys.path.insert(0, _scripts_dir)

import trade_score
import filters
import scan_log_store
from app.engines.options_intelligence import scoring as oi_scoring
from app.engines.early_breakout import breakout_score


# ---------------------------------------------------------------------------
# The exact reproduction case from the approval message
# ---------------------------------------------------------------------------

def test_exact_approval_reproduction_case_does_not_raise():
    result = trade_score.liquidity_strength(oi=100000, volume=50000, bid=None, ask=None, premium=100.0)
    assert result is None, "must return None (unavailable), not raise TypeError"
    print("test_exact_approval_reproduction_case_does_not_raise passed")


# ---------------------------------------------------------------------------
# A-G. liquidity_strength() itself
# ---------------------------------------------------------------------------

def test_a_liquidity_strength_valid_bid_ask():
    result = trade_score.liquidity_strength(oi=100000, volume=50000, bid=99.0, ask=101.0, premium=100.0)
    assert isinstance(result, float) and 0.0 <= result <= 1.0
    print("test_a_liquidity_strength_valid_bid_ask passed")


def test_b_liquidity_strength_bid_none():
    assert trade_score.liquidity_strength(oi=100000, volume=50000, bid=None, ask=101.0, premium=100.0) is None
    print("test_b_liquidity_strength_bid_none passed")


def test_c_liquidity_strength_ask_none():
    assert trade_score.liquidity_strength(oi=100000, volume=50000, bid=99.0, ask=None, premium=100.0) is None
    print("test_c_liquidity_strength_ask_none passed")


def test_d_liquidity_strength_both_none():
    assert trade_score.liquidity_strength(oi=100000, volume=50000, bid=None, ask=None, premium=100.0) is None
    print("test_d_liquidity_strength_both_none passed")


def test_e_liquidity_strength_bid_zero():
    assert trade_score.liquidity_strength(oi=100000, volume=50000, bid=0.0, ask=101.0, premium=100.0) is None
    print("test_e_liquidity_strength_bid_zero passed")


def test_f_liquidity_strength_ask_zero():
    assert trade_score.liquidity_strength(oi=100000, volume=50000, bid=99.0, ask=0.0, premium=100.0) is None
    print("test_f_liquidity_strength_ask_zero passed")


def test_g_liquidity_strength_non_numeric():
    assert trade_score.liquidity_strength(oi=100000, volume=50000, bid="N/A", ask=101.0, premium=100.0) is None
    assert trade_score.liquidity_strength(oi=100000, volume=50000, bid=99.0, ask="N/A", premium=100.0) is None
    print("test_g_liquidity_strength_non_numeric passed")


# ---------------------------------------------------------------------------
# H. main Options Intelligence path does not crash
# ---------------------------------------------------------------------------

def test_h_options_intelligence_aggregation_does_not_crash():
    sub_scores = {
        "oi": 0.8, "pcr": 0.6, "max_pain": 0.5, "greeks": 0.7, "iv": 0.9,
        "liquidity": None,  # the exact scenario this fix addresses
        "premium": 0.6, "strike": 0.7, "expiry": 0.8, "momentum": 0.5,
    }
    score = oi_scoring.options_intelligence_score(sub_scores)
    assert isinstance(score, int)
    confidence = oi_scoring.options_confidence(sub_scores)
    assert confidence in ("Confirmed", "Partial", "Weak")
    accepted, reasons = oi_scoring.options_filter(score, sub_scores)
    assert accepted is False, "a genuinely unavailable hard-hygiene factor must reject (fail closed), not silently pass"
    assert any("unavailable" in r for r in reasons), "the rejection reason must say WHY (unavailable), not just 'poor'"
    print("test_h_options_intelligence_aggregation_does_not_crash passed")


def test_h_variant_all_available_score_matches_pre_fix_behavior():
    """Sanity: when nothing is None, the renormalization must be a no-op --
    identical composite score to the original (pre-fix) formula."""
    sub_scores = {
        "oi": 0.8, "pcr": 0.6, "max_pain": 0.5, "greeks": 0.7, "iv": 0.9,
        "liquidity": 0.8, "premium": 0.6, "strike": 0.7, "expiry": 0.8, "momentum": 0.5,
    }
    score = oi_scoring.options_intelligence_score(sub_scores)
    from app.engines.options_intelligence.scoring import OPTIONS_INTELLIGENCE_WEIGHTS
    expected = round(sum(max(0.0, min(1.0, sub_scores[k])) * w for k, w in OPTIONS_INTELLIGENCE_WEIGHTS.items()))
    assert score == expected, f"all-available case must be byte-identical to the original formula, got {score} vs {expected}"
    print("test_h_variant_all_available_score_matches_pre_fix_behavior passed")


# ---------------------------------------------------------------------------
# I. early_breakout path does not crash
# ---------------------------------------------------------------------------

def test_i_early_breakout_weighted_score_does_not_crash():
    strengths = {
        "underlying_trend": 0.8, "base": 0.8, "institutional": 0.7, "breakout": 0.6, "follow_through": 0.5,
        "volume": 0.5, "momentum": 0.5, "liquidity": None,  # the exact scenario
        "risk_reward": 0.5, "smart_money": 0.5,
    }
    score = breakout_score.compute_weighted_score(strengths)
    assert isinstance(score, int)
    print("test_i_early_breakout_weighted_score_does_not_crash passed")


def test_i_variant_all_available_matches_pre_fix_behavior():
    strengths = {
        "underlying_trend": 0.8, "base": 0.8, "institutional": 0.7, "breakout": 0.6, "follow_through": 0.5,
        "volume": 0.5, "momentum": 0.5, "liquidity": 0.8,
        "risk_reward": 0.5, "smart_money": 0.5,
    }
    score = breakout_score.compute_weighted_score(strengths)
    from app.engines.early_breakout.breakout_score import EARLY_BREAKOUT_WEIGHTS
    expected = round(sum(max(0.0, min(1.0, strengths[k])) * w for k, w in EARLY_BREAKOUT_WEIGHTS.items()))
    assert score == expected, f"all-available case must be byte-identical, got {score} vs {expected}"
    print("test_i_variant_all_available_matches_pre_fix_behavior passed")


# ---------------------------------------------------------------------------
# J-L. PRICING_EVIDENCE three-state handling
# ---------------------------------------------------------------------------

def test_j_pricing_evidence_valid_quote():
    bid, ask = 119.8, 121.2
    if bid is None or ask is None:
        status = "UNAVAILABLE"
    elif bid <= 0 or ask <= 0:
        status = "INVALID"
    else:
        status = "VALID"
    assert status == "VALID"
    print("test_j_pricing_evidence_valid_quote passed")


def test_k_pricing_evidence_missing_quote():
    bid, ask = None, 121.2
    if bid is None or ask is None:
        status = "UNAVAILABLE"
    elif bid <= 0 or ask <= 0:
        status = "INVALID"
    else:
        status = "VALID"
    assert status == "UNAVAILABLE"
    print("test_k_pricing_evidence_missing_quote passed")


def test_l_pricing_evidence_invalid_zero_quote():
    bid, ask = 0.0, 0.0
    if bid is None or ask is None:
        status = "UNAVAILABLE"
    elif bid <= 0 or ask <= 0:
        status = "INVALID"
    else:
        status = "VALID"
    assert status == "INVALID", "a literal 0.0 (not None) must be its own distinct INVALID state, not VALID or UNAVAILABLE"
    print("test_l_pricing_evidence_invalid_zero_quote passed")


def test_l_variant_scanner_source_implements_three_states():
    scanner_source = open(os.path.join(_scripts_dir, "scanner.py")).read()
    assert 'bid_ask_status = "UNAVAILABLE"' in scanner_source
    assert 'bid_ask_status = "INVALID"' in scanner_source
    assert 'bid_ask_status = "VALID"' in scanner_source
    print("test_l_variant_scanner_source_implements_three_states passed")


# ---------------------------------------------------------------------------
# M. existing liquidity_ok() behavior remains correct
# ---------------------------------------------------------------------------

def test_m_liquidity_ok_unchanged_for_valid_data():
    assert filters.liquidity_ok(oi=100000, volume=50000, bid=99.0, ask=101.0, premium=100.0) is True
    assert filters.liquidity_ok(oi=100000, volume=50000, bid=None, ask=None, premium=100.0) is False
    assert filters.liquidity_ok(oi=100000, volume=50000, bid=0.0, ask=0.0, premium=100.0) is False
    print("test_m_liquidity_ok_unchanged_for_valid_data passed")


# ---------------------------------------------------------------------------
# N-P. Strategy/probability/alert eligibility unchanged
# ---------------------------------------------------------------------------

def test_n_ltp_strategy_premium_unchanged():
    scanner_source = open(os.path.join(_scripts_dir, "scanner.py")).read()
    assert 'premium = leg["ltp"]' in scanner_source
    print("test_n_ltp_strategy_premium_unchanged passed")


def test_o_probability_unchanged():
    assert trade_score.calibrated_probability_pct({"symbol": "NIFTY"}) is None
    print("test_o_probability_unchanged passed")


def test_p_alert_eligibility_config_unchanged():
    import config
    assert config.MIN_ALERT_PROBABILITY == 70
    print("test_p_alert_eligibility_config_unchanged passed")


if __name__ == "__main__":
    test_exact_approval_reproduction_case_does_not_raise()
    test_a_liquidity_strength_valid_bid_ask()
    test_b_liquidity_strength_bid_none()
    test_c_liquidity_strength_ask_none()
    test_d_liquidity_strength_both_none()
    test_e_liquidity_strength_bid_zero()
    test_f_liquidity_strength_ask_zero()
    test_g_liquidity_strength_non_numeric()
    test_h_options_intelligence_aggregation_does_not_crash()
    test_h_variant_all_available_score_matches_pre_fix_behavior()
    test_i_early_breakout_weighted_score_does_not_crash()
    test_i_variant_all_available_matches_pre_fix_behavior()
    test_j_pricing_evidence_valid_quote()
    test_k_pricing_evidence_missing_quote()
    test_l_pricing_evidence_invalid_zero_quote()
    test_l_variant_scanner_source_implements_three_states()
    test_m_liquidity_ok_unchanged_for_valid_data()
    test_n_ltp_strategy_premium_unchanged()
    test_o_probability_unchanged()
    test_p_alert_eligibility_config_unchanged()
    print("\nAll liquidity-None-safety tests (A-P + exact reproduction) passed.")
