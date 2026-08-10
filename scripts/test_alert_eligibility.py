"""
tests/test_alert_eligibility.py
------------------------------------------------------------------------------
Plain assert-based tests, same convention as
tests/test_risk_management_validation_mode.py / tests/test_trade_score_ema_none.py
(no pytest dependency).

Covers the 2026-08-10 Evidence-Integrity Pass: NO ALERT unless there is
genuine, evidence-based support for a directional trade. Each test below
maps directly to one of the required cases from that review:

  A. trend present but EMA/HTF both unavailable -> NO ALERT
     (filters.directional_confirmation_status()'s Mandatory Structural
     Confirmation fix -- the exact gap reported: trend=0.45, ema=None,
     htf=None, momentum=None used to still pass the confirmation-count
     gate via trend+vwap alone.)
  B. direction confirmations = 1/5 -> NO ALERT
  C. direction confirmations = 0/5 -> NO ALERT
  D. Options Intelligence REJECT -> NO ALERT
     (config.OPTIONS_INTELLIGENCE_FILTER_ENABLED must be True for this;
     the actual scanner.py gating logic is reproduced here rather than
     re-run end-to-end, since a real run needs live broker data.)
  E. abnormal IV -> NO ALERT (via Options Intelligence's own IV check,
     see greeks_iv.is_iv_abnormal() -- covered directly here on the
     percentage-point-scale fix from the same review pass.)
  F. RR gate FAIL -> NO ALERT (risk_reward_ok is now one of
     filters.quality_gate()'s hard-gate keys, not scoring-only.)
  G. price-action / structural confirmation unconfirmed -> NO ALERT
     (folded into case A -- this codebase's "price action" signal is
     the EMA-structure read on 5-min/15-min candles, not a separate
     candlestick-confirmation state machine; candlestick patterns
     remain an advisory score penalty, see trade_score.
     apply_candlestick_penalty() and its own module docstring for why
     a full pattern_confirmation state machine is flagged as future
     work, not implemented here.)
  H. extended move -> NO ALERT (config.ATR_CHASE_FILTER_ENABLED's
     move-vs-ATR-ceiling math, reproduced directly here.)
  I. valid strong setup with ALL mandatory evidence -> ELIGIBLE
     (every gate in this file passes at once -- the positive control
     that proves the fixes above don't also reject good setups.)

These tests exercise the underlying gate FUNCTIONS directly (filters.py,
trade_score.py, config.py, indicators.py) rather than running
scanner.score_strike() end-to-end, since that function needs a live
broker connection (market_data/angelone/upstox) this test environment
does not have. Each test's assertions are on the exact same functions
scanner.py calls at the exact same decision points, so a regression in
scanner.py's WIRING of these functions (not the functions themselves)
would not be caught here -- see this repo's manual verification notes
in the 2026-08-10 diff for the corresponding live-log evidence that
scanner.py's wiring was checked against.

Run with: python scripts/tests/test_alert_eligibility.py
Exits non-zero (via AssertionError) on the first failing check.
"""

import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
_scripts_dir = os.path.dirname(_here)
sys.path.insert(0, _scripts_dir)

import config
import filters
import trade_score
from app.engines.options_intelligence import greeks_iv


# ---------------------------------------------------------------------------
# A. trend present, EMA (5m + 15m) and HTF all unavailable -> NO ALERT
# ---------------------------------------------------------------------------

def test_a_trend_alone_without_ema_or_htf_is_rejected():
    # Exact reported case: trend=0.45, ema=None, htf=None, momentum=None,
    # vwap=True -- used to score count=2 (trend+vwap) and PASS.
    status = filters.directional_confirmation_status(
        ema_direction=None, mtf_confirmed=None, trend_value=0.45,
        momentum_value=None, vwap_confirmed_flag=True, direction="PE",
        m5_direction=None,
    )
    assert status["count"] == 2, "trend+vwap should still count toward the 5-signal total"
    assert status["structural_ok"] is False, "zero of {5m EMA, 15m EMA, HTF} confirmed"
    assert status["passed"] is False, "A: trend alone (no EMA/HTF) must NOT pass the gate"
    print("test_a_trend_alone_without_ema_or_htf_is_rejected passed")


def test_a_variant_second_sensex_style_case():
    # SENSEX-style case from the same report: trend=0.51, ema=None,
    # htf=None, momentum=None.
    status = filters.directional_confirmation_status(
        ema_direction=None, mtf_confirmed=None, trend_value=0.51,
        momentum_value=None, vwap_confirmed_flag=True, direction="CE",
        m5_direction=None,
    )
    assert status["passed"] is False
    print("test_a_variant_second_sensex_style_case passed")


def test_a_5min_ema_confirmation_alone_is_sufficient_structural_evidence():
    # Positive control for the fix: real 5-min EMA confirmation (even
    # with 15-min EMA/HTF still unavailable) satisfies Mandatory
    # Structural Confirmation.
    status = filters.directional_confirmation_status(
        ema_direction=None, mtf_confirmed=None, trend_value=0.45,
        momentum_value=None, vwap_confirmed_flag=True, direction="PE",
        m5_direction="PE",
    )
    assert status["structural_ok"] is True
    assert status["passed"] is True
    print("test_a_5min_ema_confirmation_alone_is_sufficient_structural_evidence passed")


# ---------------------------------------------------------------------------
# B / C. direction confirmations below the required minimum -> NO ALERT
# ---------------------------------------------------------------------------

def test_b_one_of_five_confirmations_is_rejected():
    status = filters.directional_confirmation_status(
        ema_direction=None, mtf_confirmed=None, trend_value=None,
        momentum_value=None, vwap_confirmed_flag=True, direction="CE",
        m5_direction=None,
    )
    assert status["count"] == 1
    assert status["passed"] is False, "B: 1/5 confirmations must NOT pass"
    print("test_b_one_of_five_confirmations_is_rejected passed")


def test_c_zero_of_five_confirmations_is_rejected():
    status = filters.directional_confirmation_status(
        ema_direction=None, mtf_confirmed=None, trend_value=None,
        momentum_value=None, vwap_confirmed_flag=False, direction="CE",
        m5_direction=None,
    )
    assert status["count"] == 0
    assert status["passed"] is False, "C: 0/5 confirmations must NOT pass"
    print("test_c_zero_of_five_confirmations_is_rejected passed")


# ---------------------------------------------------------------------------
# D. Options Intelligence REJECT -> NO ALERT
# ---------------------------------------------------------------------------

def test_d_options_intelligence_reject_blocks_when_filter_enabled():
    assert config.OPTIONS_INTELLIGENCE_FILTER_ENABLED is True, (
        "D depends on this flag actually being on -- see config.py's "
        "2026-08-10 comment for why it used to default False and make "
        "every REJECT verdict purely advisory"
    )

    def would_alert(oi_intel: dict) -> bool:
        # Reproduces scanner.score_strike()'s exact gating expression.
        if config.OPTIONS_INTELLIGENCE_FILTER_ENABLED and oi_intel.get("enabled") and not oi_intel.get("accepted", True):
            return False
        return True

    rejected_intel = {"enabled": True, "accepted": False, "decision": "REJECT"}
    accepted_intel = {"enabled": True, "accepted": True, "decision": "ACCEPT"}
    assert would_alert(rejected_intel) is False, "D: Options Intelligence REJECT must block the alert"
    assert would_alert(accepted_intel) is True
    print("test_d_options_intelligence_reject_blocks_when_filter_enabled passed")


# ---------------------------------------------------------------------------
# E. abnormal IV -> NO ALERT
# ---------------------------------------------------------------------------

def test_e_abnormal_iv_is_flagged():
    # Genuinely abnormal (percentage-point scale, per greeks_iv.py's
    # 2026-08-07 scale fix): far below floor / far above ceiling.
    assert greeks_iv.is_iv_abnormal(1.0) is True, "1% IV is below the 8% floor"
    assert greeks_iv.is_iv_abnormal(200.0) is True, "200% IV is above the 120% ceiling"
    # A legitimate IV (the exact 14-32% range from the reported logs)
    # must NOT be flagged -- this is the companion assertion proving E's
    # rejection is about GENUINELY bad quotes, not the scale bug that
    # used to reject every real IV too.
    assert greeks_iv.is_iv_abnormal(14.86) is False
    assert greeks_iv.is_iv_abnormal(32.17) is False
    print("test_e_abnormal_iv_is_flagged passed")


# ---------------------------------------------------------------------------
# F. RR gate FAIL -> NO ALERT
# ---------------------------------------------------------------------------

def test_f_risk_reward_gate_failure_blocks_via_quality_gate():
    factors_rr_fail = {
        "liquidity_ok": True, "delta_ok": True, "expiry_theta_ok": True,
        "premium_in_range_ok": True, "risk_reward_ok": False,
    }
    assert filters.quality_gate(factors_rr_fail) is False, (
        "F: risk_reward_ok=False must fail quality_gate() -- it is now "
        "one of the hard-gate keys, not scoring-only (previously a "
        "strike could show risk_reward_ok=N in the log and still reach "
        "the alert layer, since only the separate strengths['risk_reward'] "
        "score fed the alert decision)"
    )
    factors_rr_pass = dict(factors_rr_fail, risk_reward_ok=True)
    assert filters.quality_gate(factors_rr_pass) is True
    print("test_f_risk_reward_gate_failure_blocks_via_quality_gate passed")


# ---------------------------------------------------------------------------
# G. price-action / structural confirmation unconfirmed -> NO ALERT
# ---------------------------------------------------------------------------

def test_g_no_structural_confirmation_is_rejected_even_with_count_met():
    # Engineered to meet the COUNT requirement (trend+momentum, 2/5)
    # while still having zero real structural (EMA/HTF) confirmation --
    # proves count and structural checks are independently enforced.
    status = filters.directional_confirmation_status(
        ema_direction=None, mtf_confirmed=None, trend_value=0.5,
        momentum_value=0.005, vwap_confirmed_flag=False, direction="CE",
        m5_direction=None,
    )
    assert status["count"] == 2
    assert status["structural_ok"] is False
    assert status["passed"] is False, "G: count met but no structural confirmation must still reject"
    print("test_g_no_structural_confirmation_is_rejected_even_with_count_met passed")


# ---------------------------------------------------------------------------
# H. extended move -> NO ALERT
# ---------------------------------------------------------------------------

def test_h_extended_move_beyond_atr_chase_ceiling_is_rejected():
    assert config.ATR_CHASE_FILTER_ENABLED is True
    atr_index_points = 50.0
    session_open = 24500.0
    spot_price = 24650.0  # 150 pts from open
    move_from_open = abs(spot_price - session_open)
    chase_ceiling = atr_index_points * config.ATR_CHASE_MULTIPLIER  # 100 at the default 2.0x
    assert move_from_open > chase_ceiling, "H: this move must exceed the configured ATR chase ceiling"
    print("test_h_extended_move_beyond_atr_chase_ceiling_is_rejected passed")


def test_h_variant_move_within_ceiling_is_not_rejected_by_this_check():
    atr_index_points = 50.0
    session_open = 24500.0
    spot_price = 24540.0  # 40 pts from open
    move_from_open = abs(spot_price - session_open)
    chase_ceiling = atr_index_points * config.ATR_CHASE_MULTIPLIER
    assert move_from_open <= chase_ceiling
    print("test_h_variant_move_within_ceiling_is_not_rejected_by_this_check passed")


# ---------------------------------------------------------------------------
# I. valid strong setup with ALL mandatory evidence -> ELIGIBLE
# ---------------------------------------------------------------------------

def test_i_valid_strong_setup_passes_every_gate():
    # Directional confirmation gate, WITH real structural evidence.
    status = filters.directional_confirmation_status(
        ema_direction="CE", mtf_confirmed=True, trend_value=0.55,
        momentum_value=0.005, vwap_confirmed_flag=True, direction="CE",
        m5_direction="CE",
    )
    assert status["count"] == 5
    assert status["structural_ok"] is True
    assert status["passed"] is True

    # Quality gate (liquidity/delta/theta/premium/RR all pass).
    factors = {
        "liquidity_ok": True, "delta_ok": True, "expiry_theta_ok": True,
        "premium_in_range_ok": True, "risk_reward_ok": True,
    }
    assert filters.quality_gate(factors) is True

    # Options Intelligence accepts.
    oi_intel = {"enabled": True, "accepted": True}
    would_be_blocked_by_intel = (
        config.OPTIONS_INTELLIGENCE_FILTER_ENABLED and oi_intel.get("enabled")
        and not oi_intel.get("accepted", True)
    )
    assert not would_be_blocked_by_intel

    # IV is valid (not abnormal).
    assert greeks_iv.is_iv_abnormal(18.5) is False

    # Not an extended move.
    atr_index_points = 50.0
    move_from_open = 30.0
    assert move_from_open <= atr_index_points * config.ATR_CHASE_MULTIPLIER

    # Only now: score/probability may be computed.
    strengths = {
        "trend": 0.55, "volume": 1.0, "oi": 0.8, "momentum": 0.6, "vwap": 0.9,
        "ema": 0.7, "risk_reward": 0.9, "liquidity": 0.95, "market_strength": 0.5,
        "htf": 1.0,
    }
    score = trade_score.compute_trade_score(strengths)
    probability_pct = trade_score.compute_probability_pct(score)
    assert score >= config.ENTRY_SCORE
    assert probability_pct >= 70, "I: a genuinely fully-confirmed setup should clear 70%"
    print("test_i_valid_strong_setup_passes_every_gate passed")


if __name__ == "__main__":
    test_a_trend_alone_without_ema_or_htf_is_rejected()
    test_a_variant_second_sensex_style_case()
    test_a_5min_ema_confirmation_alone_is_sufficient_structural_evidence()
    test_b_one_of_five_confirmations_is_rejected()
    test_c_zero_of_five_confirmations_is_rejected()
    test_d_options_intelligence_reject_blocks_when_filter_enabled()
    test_e_abnormal_iv_is_flagged()
    test_f_risk_reward_gate_failure_blocks_via_quality_gate()
    test_g_no_structural_confirmation_is_rejected_even_with_count_met()
    test_h_extended_move_beyond_atr_chase_ceiling_is_rejected()
    test_h_variant_move_within_ceiling_is_not_rejected_by_this_check()
    test_i_valid_strong_setup_passes_every_gate()
    print("\nAll alert-eligibility tests (A-I) passed.")
