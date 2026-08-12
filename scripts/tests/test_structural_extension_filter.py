"""
tests/test_structural_extension_filter.py
------------------------------------------------------------------------------
Plain assert-based tests, same convention as this repo's other tests/*.py
files (no pytest dependency).

Covers the 2026-08-11 Structural Extension Filter addition
(config.STRUCTURAL_EXTENSION_FILTER_ENABLED/
STRUCTURAL_EXTENSION_MAX_DISTANCE_ATR, in scanner.score_strike()) -- a
SECOND, independent extension check alongside the pre-existing ATR Chase
Filter (config.ATR_CHASE_FILTER_ENABLED/ATR_CHASE_MULTIPLIER, unchanged by
this addition -- see tests/test_extension_filter.py for that filter's own
regression tests, still passing unmodified).

Required cases (verbatim from the audit):
  A. Fresh breakout close to breakout level -> NOT structurally extended.
  B. Same breakout after a large continuation away from breakout level
     -> structurally extended -> NO ALERT.
  C. ATR extension fails -> NO ALERT regardless of structural result.
  D. Structural extension fails -> NO ALERT even if ATR extension is
     technically within threshold.
  E. No reliable S/R reference level -> structural filter does not
     manufacture a rejection; existing gates decide.
  F. High option premium alone -> must NOT trigger structural extension
     rejection.
  G. Probability >=70% -> must NOT override either extension filter.
  H. Options Intelligence accepted -> must NOT override either
     extension filter.
  I. Fully valid fresh setup -> remains eligible if every other gate
     passes.

Reuses tests/test_scanner_to_alert_pipeline.py's _run_mocked_score_strike()
harness (atr_override/spot_price/chain parameters) -- calls the REAL
scanner.score_strike(), not a reimplementation of its logic. Every test
below uses a small, EXPLICIT 2-candle fixture (not the shared ramp fixture
other test files use) so the reference level (indicators.
find_nearby_level()'s min-low-over-lookback, for this PE-direction suite)
and the session-open reference (candles_15m[0]["open"]) are both exactly
and independently controlled -- required because find_nearby_level()
always returns the MINIMUM low across the window for PE, which the shared
ramp fixture's monotonic shape doesn't let you place freely relative to a
chosen spot_price.
"""

import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
_scripts_dir = os.path.dirname(_here)
sys.path.insert(0, _scripts_dir)
sys.path.insert(0, _here)

import test_scanner_to_alert_pipeline as pipeline
import config


def _candles(session_open, level_low, level_index=5, length=25):
    """
    A realistic-length (25) 15-min candle series, matching the shape
    tests/test_scanner_to_alert_pipeline.py's own fixtures use (EMA9/21
    need >=21 candles to compute a real number instead of None, and a
    single too-short candle risks being read as a Doji, both of which
    would silently drag the composite score down for reasons unrelated
    to what this test is isolating). candles[0]["open"] becomes
    scanner.py's session_open reference (for the ATR Chase Filter);
    `level_low` is placed at `level_index` (within the last-20-candle
    lookback for length=25) as indicators.find_nearby_level()'s
    PE-direction reference (min low over the window) -- deliberately
    lower than every other candle's low so it unambiguously wins that
    min() regardless of the base ramp.
    """
    # Base ramp's lows are placed FAR above session_open (+1000) so
    # `level_low` -- which, across these tests, is always within a few
    # hundred points of session_open/spot_price -- unconditionally wins
    # min() as the smallest low in the window, regardless of whether
    # the specific test places it above or below spot_price.
    candles = [
        {"open": session_open, "high": session_open + 1010 + i,
         "low": session_open + 1000 + i, "close": session_open + 1005 + i}
        for i in range(length)
    ]
    candles[level_index]["low"] = level_low
    candles[level_index]["high"] = level_low + 10
    candles[level_index]["close"] = level_low + 5
    return candles


def _kwargs(candles_15m, spot_price, atr_override, **overrides):
    base = dict(
        ema_15m_direction="PE", ema_5m_direction="PE",
        trend_value=0.7, momentum_value=-0.01, vwap_confirmed=True,
        oi_intel_accepted=True, candles_15m=candles_15m,
        atr_override=atr_override, spot_price=spot_price,
    )
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# A. Fresh breakout close to breakout level -> NOT structurally extended
# ---------------------------------------------------------------------------
# level=24500, distance past level=85 (1.7x a 50-pt ATR -- under the 2.0x
# structural threshold) -- and, since distance_to_level (85) also clears
# the pre-existing RR_LEVEL_CHECK's own room requirement (needs >
# ATR*T1_ATR_MULT = 50*1.5 = 75), the candidate can reach full eligibility,
# not just clear the extension filter. session_open == spot_price here
# (move_from_open=0) so the ATR Chase Filter is trivially not a factor,
# isolating this test to the structural check specifically.

_LEVEL_A = 24500.0
_SPOT_A = _LEVEL_A - 85.0  # 24415.0
_CANDLES_A = _candles(session_open=_SPOT_A, level_low=_LEVEL_A)


def test_a_fresh_breakout_close_to_level_not_structurally_extended():
    result = pipeline._run_mocked_score_strike(**_kwargs(_CANDLES_A, _SPOT_A, atr_override=50.0))
    assert result is not None, "A: a fresh breakout (1.7x ATR past the level) must not be rejected"
    print("test_a_fresh_breakout_close_to_level_not_structurally_extended passed")


# ---------------------------------------------------------------------------
# B. Large continuation away from breakout level -> structurally extended
#    -> NO ALERT
# ---------------------------------------------------------------------------
# Same level (24500), but 250 points past it now (5.0x a 50-pt ATR --
# well past the 2.0x threshold). session_open == spot_price again, so
# this is a clean, ATR-uninvolved structural-only rejection.

_LEVEL_B = 24500.0
_SPOT_B = _LEVEL_B - 250.0  # 24250.0
_CANDLES_B = _candles(session_open=_SPOT_B, level_low=_LEVEL_B)


def test_b_large_continuation_past_level_is_structurally_extended():
    result = pipeline._run_mocked_score_strike(**_kwargs(_CANDLES_B, _SPOT_B, atr_override=50.0))
    assert result is None, "B: 5.0x ATR past the reference level must be rejected as structurally extended"
    print("test_b_large_continuation_past_level_is_structurally_extended passed")


# ---------------------------------------------------------------------------
# C. ATR extension fails -> NO ALERT regardless of structural result
# ---------------------------------------------------------------------------
# session_open=24600 (fixed), spot=24450 -> move_from_open=150, ATR
# ceiling=2x50=100 -- ATR EXTENDED. The reference level is placed at
# 24420, only 30 points BEHIND spot in the trade direction (structural
# distance clamps to 0 -- structurally FRESH) -- proving the ATR filter
# rejects entirely on its own, independent of a fresh structural read.

_SESSION_OPEN_C = 24600.0
_SPOT_C = 24450.0
_LEVEL_C = 24420.0  # behind spot (level - spot = -30 -> clamped to 0, structurally fresh)
_CANDLES_C = _candles(session_open=_SESSION_OPEN_C, level_low=_LEVEL_C)


def test_c_atr_extension_alone_rejects_even_when_structurally_fresh():
    result = pipeline._run_mocked_score_strike(**_kwargs(_CANDLES_C, _SPOT_C, atr_override=50.0))
    assert result is None, "C: ATR extension (150pt move > 100pt ceiling) must reject even when structurally fresh"
    print("test_c_atr_extension_alone_rejects_even_when_structurally_fresh passed")


# ---------------------------------------------------------------------------
# D. Structural extension fails -> NO ALERT even if ATR extension is
#    technically within threshold
# ---------------------------------------------------------------------------
# session_open=24600, spot=24560 -> move_from_open=40, ATR ceiling=2x30=60
# -- ATR NOT extended (well within threshold). Reference level=24630, 70
# points AHEAD of spot in the trade direction (level - spot = 70, 70/30 =
# 2.33x a 30-pt ATR) -- structurally EXTENDED despite the ATR check
# passing cleanly.

_SESSION_OPEN_D = 24600.0
_SPOT_D = 24560.0
_LEVEL_D = 24630.0
_CANDLES_D = _candles(session_open=_SESSION_OPEN_D, level_low=_LEVEL_D)


def test_d_structural_extension_alone_rejects_even_when_atr_fresh():
    result = pipeline._run_mocked_score_strike(**_kwargs(_CANDLES_D, _SPOT_D, atr_override=30.0))
    assert result is None, "D: structural extension (2.33x ATR past the level) must reject even when ATR-fresh"
    print("test_d_structural_extension_alone_rejects_even_when_atr_fresh passed")


# ---------------------------------------------------------------------------
# E. No reliable S/R reference level -> structural filter does not
#    manufacture a rejection
# ---------------------------------------------------------------------------

def test_e_no_reliable_reference_does_not_manufacture_a_rejection():
    """
    indicators.find_nearby_level() only returns None for an empty candle
    list -- unreachable through score_strike() in practice (the Candle
    Readiness Gate already requires real candles before this point).
    Tested here directly at the indicators layer instead, which is the
    honest, reachable unit of behavior: config.
    STRUCTURAL_EXTENSION_MAX_DISTANCE_ATR's own docstring specifies "NOT
    invent one, NOT auto-reject" for this case -- confirmed by reading
    scanner.score_strike()'s own structural-extension block, which only
    ever sets structural_extended=True INSIDE the `if nearby_level is
    not None` branch; the None branch logs NO_RELIABLE_REFERENCE and
    leaves structural_extended at its False default, unconditionally.
    """
    import indicators
    assert indicators.find_nearby_level([], "PE") is None
    print("test_e_no_reliable_reference_does_not_manufacture_a_rejection passed (see docstring for the reachability note)")


# ---------------------------------------------------------------------------
# F. High option premium alone -> must NOT trigger structural extension
#    rejection
# ---------------------------------------------------------------------------

def test_f_high_option_premium_alone_does_not_trigger_structural_rejection():
    high_premium_chain = [{
        "strike_price": 24600.0,
        "put_instrument_key": "NSE_FO|TESTPUT", "put_ltp": 250.0, "put_oi": 60000.0,
        "put_volume": 20000.0, "put_bid": 249.5, "put_ask": 250.5, "put_delta": -0.45,
        "call_instrument_key": "NSE_FO|TESTCALL", "call_ltp": 100.0, "call_oi": 50000.0,
        "call_volume": 15000.0, "call_bid": 99.5, "call_ask": 100.5, "call_delta": 0.45,
    }]
    result = pipeline._run_mocked_score_strike(
        **_kwargs(_CANDLES_A, _SPOT_A, atr_override=50.0, chain=high_premium_chain)
    )
    assert result is not None, "F: an elevated OPTION premium must not affect the structural extension calculation"
    assert result["alert_premium"] == 250.0, "F (sanity check): confirms the elevated-premium fixture was used"
    print("test_f_high_option_premium_alone_does_not_trigger_structural_rejection passed")


# ---------------------------------------------------------------------------
# G. Probability >=70% -> must NOT override either extension filter
# ---------------------------------------------------------------------------

def test_g_probability_cannot_override_structural_extension_rejection():
    result = pipeline._run_mocked_score_strike(**_kwargs(_CANDLES_B, _SPOT_B, atr_override=50.0))
    assert result is None, (
        "G: no probability value even exists for a structurally-rejected strike -- "
        "score_strike() returns None before strengths/score/probability are computed"
    )
    print("test_g_probability_cannot_override_structural_extension_rejection passed")


# ---------------------------------------------------------------------------
# H. Options Intelligence accepted -> must NOT override either extension
#    filter
# ---------------------------------------------------------------------------

def test_h_options_intelligence_accept_cannot_override_structural_extension():
    result = pipeline._run_mocked_score_strike(
        **_kwargs(_CANDLES_B, _SPOT_B, atr_override=50.0, oi_intel_accepted=True)
    )
    assert result is None, (
        "H: the structural extension filter runs BEFORE Options Intelligence is ever "
        "consulted -- an accepting verdict must not matter"
    )
    print("test_h_options_intelligence_accept_cannot_override_structural_extension passed")


# ---------------------------------------------------------------------------
# I. Fully valid fresh setup -> remains eligible if every other gate
#    passes
# ---------------------------------------------------------------------------

def test_i_fully_valid_fresh_setup_remains_eligible():
    result = pipeline._run_mocked_score_strike(**_kwargs(_CANDLES_A, _SPOT_A, atr_override=50.0))
    assert result is not None, "I: a genuinely fresh, fully-confirmed setup must still be eligible"
    assert result["probability_pct"] >= 70
    print("test_i_fully_valid_fresh_setup_remains_eligible passed")


if __name__ == "__main__":
    test_a_fresh_breakout_close_to_level_not_structurally_extended()
    test_b_large_continuation_past_level_is_structurally_extended()
    test_c_atr_extension_alone_rejects_even_when_structurally_fresh()
    test_d_structural_extension_alone_rejects_even_when_atr_fresh()
    test_e_no_reliable_reference_does_not_manufacture_a_rejection()
    test_f_high_option_premium_alone_does_not_trigger_structural_rejection()
    test_g_probability_cannot_override_structural_extension_rejection()
    test_h_options_intelligence_accept_cannot_override_structural_extension()
    test_i_fully_valid_fresh_setup_remains_eligible()
    print("\nAll structural-extension-filter tests (A-I) passed.")
