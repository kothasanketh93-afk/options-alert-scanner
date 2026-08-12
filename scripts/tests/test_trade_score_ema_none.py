"""
tests/test_trade_score_ema_none.py
------------------------------------------------------------------------------
Plain assert-based tests, same convention as
tests/test_risk_management_validation_mode.py (no pytest dependency).

Covers FIX 1 (2026-07-30): scanner.py used to pass 0.0 for the "ema"
strength whenever EMA9/EMA21 weren't yet computable, and
compute_trade_score() had no way to tell "confirmed 0" apart from
"unavailable" -- both dragged the weighted score down by the full EMA
weight (10/100). Now:
  - scanner.py passes None instead of 0.0 (see scanner.py's ema strengths line).
  - compute_trade_score() excludes a None factor from BOTH the
    numerator and the denominator and renormalizes the remaining
    weights back up to 100, instead of scoring it as a hard 0.
  - scoring_reasons()/factor_checklist() (display helpers) no longer
    raise on a None value.

Every check here also asserts that scoring for the OTHER
(non-None) factors is completely unchanged -- this fix must not touch
how any available factor is scored.

Run with: python scripts/tests/test_trade_score_ema_none.py
Exits non-zero (via AssertionError) on the first failing check.
"""

import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
_scripts_dir = os.path.dirname(_here)
sys.path.insert(0, _scripts_dir)

import trade_score
from config import TRADE_SCORE_WEIGHTS


def _all_factors_at(value: float) -> dict:
    return {key: value for key in TRADE_SCORE_WEIGHTS}


def test_all_factors_at_1_scores_100_regardless_of_fix():
    strengths = _all_factors_at(1.0)
    assert trade_score.compute_trade_score(strengths) == 100
    print("test_all_factors_at_1_scores_100_regardless_of_fix passed")


def test_all_factors_at_0_scores_0():
    strengths = _all_factors_at(0.0)
    assert trade_score.compute_trade_score(strengths) == 0
    print("test_all_factors_at_0_scores_0 passed")


def test_ema_none_excludes_weight_instead_of_scoring_zero():
    """
    Core regression check: every factor at 1.0 EXCEPT ema=None should
    score 100 (renormalized), not 90 (the old "None coerced to 0.0"
    behavior would have produced 90/100 -- full marks on every
    AVAILABLE factor, minus the full 10-point EMA weight).
    """
    strengths = _all_factors_at(1.0)
    strengths["ema"] = None
    score = trade_score.compute_trade_score(strengths)
    assert score == 100, f"expected 100 (EMA excluded+renormalized), got {score}"
    print("test_ema_none_excludes_weight_instead_of_scoring_zero passed")


def test_ema_none_vs_ema_confirmed_zero_are_different_scores():
    """
    The bug this fixes, stated directly: "EMA not yet known" (None)
    and "EMA confirmed pointing the wrong way" (0.0) must NOT produce
    the same trade score.
    """
    strengths_none = _all_factors_at(1.0)
    strengths_none["ema"] = None

    strengths_zero = _all_factors_at(1.0)
    strengths_zero["ema"] = 0.0

    score_none = trade_score.compute_trade_score(strengths_none)
    score_zero = trade_score.compute_trade_score(strengths_zero)

    assert score_none == 100
    assert score_zero == 90  # unchanged old behavior for a REAL (confirmed) 0.0
    assert score_none != score_zero
    print("test_ema_none_vs_ema_confirmed_zero_are_different_scores passed")


def test_missing_key_still_scores_zero_backward_compat():
    """
    A key that's simply ABSENT from the dict (not explicitly None)
    must keep scoring 0 for that factor, per compute_trade_score()'s
    pre-existing documented convention -- this fix only changes the
    behavior for an explicit None, never for a missing key.
    """
    strengths = _all_factors_at(1.0)
    del strengths["ema"]
    score = trade_score.compute_trade_score(strengths)
    assert score == 90, f"expected 90 (missing key still scores 0), got {score}"
    print("test_missing_key_still_scores_zero_backward_compat passed")


def test_available_factor_scoring_math_unchanged():
    """
    Partial strengths on AVAILABLE factors must weight identically to
    before this fix -- this fix only changes how UNAVAILABLE (None)
    factors are treated, never the math for present values.
    """
    strengths = _all_factors_at(0.0)
    strengths["trend"] = 1.0  # weight 20
    strengths["volume"] = 0.5  # weight 15 -> 7.5
    score = trade_score.compute_trade_score(strengths)
    assert score == 28, f"expected 28 (20 + 7.5, rounded), got {score}"
    print("test_available_factor_scoring_math_unchanged passed")


def test_multiple_none_factors_renormalize_correctly():
    strengths = _all_factors_at(1.0)
    strengths["ema"] = None       # weight 10
    strengths["htf"] = None       # weight 5
    # Remaining available weight: 100 - 10 - 5 = 85, all at strength 1.0
    score = trade_score.compute_trade_score(strengths)
    assert score == 100
    print("test_multiple_none_factors_renormalize_correctly passed")


def test_every_factor_none_scores_zero_not_a_crash():
    strengths = {key: None for key in TRADE_SCORE_WEIGHTS}
    score = trade_score.compute_trade_score(strengths)
    assert score == 0
    print("test_every_factor_none_scores_zero_not_a_crash passed")


def test_scoring_reasons_does_not_crash_on_none_and_skips_it():
    strengths = _all_factors_at(1.0)
    strengths["ema"] = None
    reasons = trade_score.scoring_reasons(strengths)
    assert all("EMA" not in r for r in reasons), "None (unavailable) EMA should not produce a reason line"
    assert len(reasons) == len(TRADE_SCORE_WEIGHTS) - 1
    print("test_scoring_reasons_does_not_crash_on_none_and_skips_it passed")


def test_factor_checklist_skips_none_instead_of_showing_a_permanent_failing_mark():
    """
    BUGFIX (2026-08-11): used to force None -> 0.0 -> "⚠️" -- correct-
    looking for a factor that's merely unavailable THIS cycle, but
    actively misleading for a factor that's ALWAYS None (e.g. "htf",
    since 2026-08-11 -- see filters.directional_confirmation_status()'s
    docstring for why no genuine independent higher-timeframe source
    exists in this codebase): showing "HTF ⚠️" on every alert forever
    reads as "checked and weak", not "never checked". Skipped entirely
    now, matching scoring_reasons()'s existing "no reason line for an
    unavailable factor" behavior (see the test just above this one).
    """
    strengths = _all_factors_at(1.0)
    strengths["ema"] = None
    lines = trade_score.factor_checklist(strengths)
    ema_lines = [l for l in lines if l.startswith("EMA ")]
    assert ema_lines == [], "EMA unavailable this cycle should produce NO checklist line, not a '⚠️' one"
    print("test_factor_checklist_skips_none_instead_of_showing_a_permanent_failing_mark passed")


if __name__ == "__main__":
    test_all_factors_at_1_scores_100_regardless_of_fix()
    test_all_factors_at_0_scores_0()
    test_ema_none_excludes_weight_instead_of_scoring_zero()
    test_ema_none_vs_ema_confirmed_zero_are_different_scores()
    test_missing_key_still_scores_zero_backward_compat()
    test_available_factor_scoring_math_unchanged()
    test_multiple_none_factors_renormalize_correctly()
    test_every_factor_none_scores_zero_not_a_crash()
    test_scoring_reasons_does_not_crash_on_none_and_skips_it()
    test_factor_checklist_skips_none_instead_of_showing_a_permanent_failing_mark()
    print("\nAll trade_score EMA-None tests passed.")
