"""
tests/test_extension_filter.py
------------------------------------------------------------------------------
Plain assert-based tests, same convention as this repo's other tests/*.py
files (no pytest dependency).

Covers the 2026-08-11 "final focused audit" of the "do not enter after an
already extended move" requirement -- config.ATR_CHASE_FILTER_ENABLED /
config.ATR_CHASE_MULTIPLIER, implemented in scanner.score_strike() (see
that function's own "--- ATR Chase Filter ---" comment block).

Required cases (verbatim from the audit):
  A. Normal fresh breakout -> eligible if all other gates pass.
  B. Large already-extended underlying move -> NO ALERT.
  C. Large option premium move but underlying is not extended -> do not
     incorrectly reject solely because of option premium.
  D. Probability >=70% must NOT override extension rejection.
  E. Extension rejection must remain effective even when Options
     Intelligence accepts.

These call the REAL scanner.score_strike() (via
tests/test_scanner_to_alert_pipeline.py's _run_mocked_score_strike()
harness, reused here rather than duplicated) with the underlying's session-
open/current price and today's ATR independently controllable -- see that
harness's `atr_override`/`spot_price` parameters (added for this audit).
Option premium (the chain fixture's `put_ltp`/`call_ltp`) is never varied
by the ATR Chase Filter's own inputs at all (see the audit report this file
accompanies) -- test C demonstrates that directly by using a chain with a
large premium while the underlying itself stays un-extended.

Run with: python scripts/tests/test_extension_filter.py
Exits non-zero (via AssertionError) on the first failing check.
"""

import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
_scripts_dir = os.path.dirname(_here)
sys.path.insert(0, _scripts_dir)

sys.path.insert(0, _here)  # so `import test_scanner_to_alert_pipeline` resolves
import test_scanner_to_alert_pipeline as pipeline

import config

# Session-open-aligned candle fixture with a FAR support/resistance level
# (per pipeline._FAR_LEVEL_CANDLES_15M) so the RR "room to level" gate
# passes cleanly in every test below -- isolates each test to the ATR
# Chase Filter specifically, not an incidental RR failure (same isolation
# pattern the other structural-gate tests already use).
_CANDLES = pipeline._FAR_LEVEL_CANDLES_15M
_SESSION_OPEN = _CANDLES[0]["open"]  # 24600.0, per pipeline._make_candles_15m()


def _fully_confirmed_kwargs(spot_price, atr_override):
    """Common kwargs for a candidate that would otherwise clear every
    OTHER gate (structural confirmation, RR, Options Intelligence) --
    isolates each test below to the ATR Chase Filter's own pass/fail."""
    return dict(
        ema_15m_direction="PE", ema_5m_direction="PE",
        trend_value=0.7, momentum_value=-0.01, vwap_confirmed=True,
        oi_intel_accepted=True, candles_15m=_CANDLES,
        atr_override=atr_override, spot_price=spot_price,
    )


# ---------------------------------------------------------------------------
# A. Normal fresh breakout -> eligible if all other gates pass
# ---------------------------------------------------------------------------

def test_a_fresh_breakout_within_atr_ceiling_is_eligible():
    atr = 50.0
    spot_price = _SESSION_OPEN + 40.0  # well inside 2x ATR = 100
    result = pipeline._run_mocked_score_strike(**_fully_confirmed_kwargs(spot_price, atr))
    assert result is not None, "A: a fresh, non-extended move must NOT be rejected by the ATR Chase Filter"
    print("test_a_fresh_breakout_within_atr_ceiling_is_eligible passed")


# ---------------------------------------------------------------------------
# B. Large already-extended underlying move -> NO ALERT
# ---------------------------------------------------------------------------

def test_b_large_extended_underlying_move_is_rejected():
    atr = 50.0
    spot_price = _SESSION_OPEN + 150.0  # 150 pts > 2x ATR = 100 -- extended
    result = pipeline._run_mocked_score_strike(**_fully_confirmed_kwargs(spot_price, atr))
    assert result is None, "B: a move beyond ATR_CHASE_MULTIPLIER x ATR from session open must be rejected"
    print("test_b_large_extended_underlying_move_is_rejected passed")


# ---------------------------------------------------------------------------
# C. Large option premium move but underlying is NOT extended -> do not
#    incorrectly reject solely because of option premium
# ---------------------------------------------------------------------------

def test_c_large_premium_move_alone_does_not_trigger_extension_rejection():
    """
    The ATR Chase Filter's inputs are exclusively UNDERLYING spot_price
    and UNDERLYING ATR (see scanner.score_strike()'s implementation --
    `move_from_open = abs(spot_price - session_open)`,
    `chase_ceiling = atr_index_points * ATR_CHASE_MULTIPLIER`, both
    computed from the underlying's own 15-min candles). Option premium
    (the chain fixture's put_ltp=120.0) never enters this calculation at
    all. Demonstrated here by using a chain with an ELEVATED premium
    (250.0, still within config.PREMIUM_RANGE for NIFTY) while the
    underlying itself stays well within the ATR ceiling -- the candidate
    must still be evaluated on its own merits, not rejected for a reason
    that has nothing to do with the underlying's actual movement.
    """
    atr = 50.0
    spot_price = _SESSION_OPEN + 20.0  # well inside 2x ATR = 100
    high_premium_chain = [{
        "strike_price": 24600.0,
        "put_instrument_key": "NSE_FO|TESTPUT", "put_ltp": 250.0, "put_oi": 60000.0,
        "put_volume": 20000.0, "put_bid": 249.5, "put_ask": 250.5, "put_delta": -0.45,
        "call_instrument_key": "NSE_FO|TESTCALL", "call_ltp": 100.0, "call_oi": 50000.0,
        "call_volume": 15000.0, "call_bid": 99.5, "call_ask": 100.5, "call_delta": 0.45,
    }]
    kwargs = _fully_confirmed_kwargs(spot_price, atr)
    kwargs["chain"] = high_premium_chain
    result = pipeline._run_mocked_score_strike(**kwargs)
    assert result is not None, (
        "C: an elevated OPTION premium alone must not trigger the ATR Chase Filter -- "
        "it only looks at the underlying's own price/ATR"
    )
    assert result["alert_premium"] == 250.0, (
        "C (sanity check on the test itself): confirms the elevated-premium chain "
        "fixture was actually used, not silently ignored"
    )
    print("test_c_large_premium_move_alone_does_not_trigger_extension_rejection passed")


# ---------------------------------------------------------------------------
# D. Probability >=70% must NOT override extension rejection
# ---------------------------------------------------------------------------

def test_d_probability_cannot_override_extension_rejection():
    """
    The ATR Chase Filter runs and returns None (see scanner.
    score_strike()'s code order: readiness -> hard gates -> directional
    confirmation -> ATR CHASE FILTER -> score/strengths -> probability)
    BEFORE strengths/score/probability are ever computed for a rejected
    strike -- there is no probability value at all for main.py's
    MIN_ALERT_PROBABILITY floor to compare against, because
    score_strike() already returned None. This test confirms the
    end-to-end result (None) for a setup that -- absent the extended
    move -- would have cleared every other gate cleanly (same
    structural/RR/Options-Intelligence inputs as test A, which DOES
    return a candidate when the move is normal).
    """
    atr = 50.0
    spot_price = _SESSION_OPEN + 150.0  # same extended move as test B
    result = pipeline._run_mocked_score_strike(**_fully_confirmed_kwargs(spot_price, atr))
    assert result is None, "D: no probability, however high it would have been, can rescue an ATR-chase rejection"
    print("test_d_probability_cannot_override_extension_rejection passed")


# ---------------------------------------------------------------------------
# E. Extension rejection must remain effective even when Options
#    Intelligence accepts
# ---------------------------------------------------------------------------

def test_e_extension_rejection_effective_even_with_options_intelligence_accept():
    atr = 50.0
    spot_price = _SESSION_OPEN + 150.0
    kwargs = _fully_confirmed_kwargs(spot_price, atr)
    kwargs["oi_intel_accepted"] = True  # explicit: Options Intelligence would ACCEPT this contract
    result = pipeline._run_mocked_score_strike(**kwargs)
    assert result is None, (
        "E: the ATR Chase Filter runs BEFORE Options Intelligence is ever consulted "
        "(score_strike() has already returned None by that point) -- an accepting "
        "Options Intelligence verdict must not matter"
    )
    print("test_e_extension_rejection_effective_even_with_options_intelligence_accept passed")


if __name__ == "__main__":
    test_a_fresh_breakout_within_atr_ceiling_is_eligible()
    test_b_large_extended_underlying_move_is_rejected()
    test_c_large_premium_move_alone_does_not_trigger_extension_rejection()
    test_d_probability_cannot_override_extension_rejection()
    test_e_extension_rejection_effective_even_with_options_intelligence_accept()
    print("\nAll extension-filter (ATR Chase) tests (A-E) passed.")
