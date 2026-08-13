"""
app/engines/options_intelligence/tests/test_options_intelligence.py
------------------------------------------------------------------------------
Plain assert-based tests, no pytest dependency -- same convention as
app/engines/early_breakout/tests/test_engine.py. All synthetic,
deterministic data, no broker/network access.

Covers: Option Chain / PCR / Max Pain / OI Engines (chain_metrics.py),
Greeks / IV Engines (greeks_iv.py), Liquidity / Premium Engines
(liquidity_premium.py), Strike / Expiry Engines (strike_expiry.py),
Momentum Engine (momentum.py), Confirmation/Scoring/Filter Engines
(scoring.py), and the end-to-end analyze() entry point (engine.py) --
unit tests for the pure-math modules, plus replay/regression/edge-case
checks at the engine.analyze() level.

Run with: python scripts/app/engines/options_intelligence/tests/test_options_intelligence.py
Exits non-zero (via AssertionError) on the first failing check.
"""

import sys
import os

_here = os.path.dirname(os.path.abspath(__file__))
_scripts_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_here))))
sys.path.insert(0, _scripts_dir)

from app.engines.options_intelligence import (
    chain_metrics, greeks_iv, liquidity_premium, strike_expiry, momentum, scoring, engine,
)


def make_chain(rows):
    """rows: list of (strike, call_oi, put_oi, call_ltp, put_ltp) tuples.
    Produces a chain in the same flat shape option_chain._extract_leg()
    reads (call_/put_ prefixed keys per strike)."""
    chain = []
    for strike, call_oi, put_oi, call_ltp, put_ltp in rows:
        chain.append({
            "strike_price": strike,
            "call_instrument_key": f"CE|{strike}",
            "put_instrument_key": f"PE|{strike}",
            "call_oi": call_oi, "put_oi": put_oi,
            "call_ltp": call_ltp, "put_ltp": put_ltp,
            "call_volume": 5000, "put_volume": 5000,
            "call_bid": call_ltp - 0.5, "call_ask": call_ltp + 0.5,
            "put_bid": put_ltp - 0.5, "put_ask": put_ltp + 0.5,
            "call_delta": 0.5, "put_delta": -0.5,
            "call_gamma": 0.001, "put_gamma": 0.001,
            "call_theta": -2.0, "put_theta": -2.0,
            "call_vega": 3.0, "put_vega": 3.0,
            "call_iv": 0.18, "put_iv": 0.18,
        })
    return chain


def check(condition, message):
    assert condition, f"FAILED: {message}"
    print(f"PASS: {message}")


# ---------------------------------------------------------------------------
# Option Chain / PCR / Max Pain / OI Engines
# ---------------------------------------------------------------------------

def test_total_oi_and_pcr():
    chain = make_chain([
        (24000, 10_000, 20_000, 300, 50),
        (24100, 5_000, 5_000, 200, 100),
    ])
    check(chain_metrics.total_call_oi(chain) == 15_000, "total_call_oi sums both strikes")
    check(chain_metrics.total_put_oi(chain) == 25_000, "total_put_oi sums both strikes")
    pcr = chain_metrics.overall_pcr(chain)
    check(abs(pcr - (25_000 / 15_000)) < 1e-9, "overall_pcr = total put OI / total call OI")
    check(chain_metrics.market_bias(pcr) == "Bearish", "PCR > PCR_BEARISH_MIN reads Bearish")
    check(chain_metrics.overall_pcr([]) == 0.0, "overall_pcr degrades to 0.0 on an empty chain, not a crash")


def test_pcr_score_direction_sensitivity():
    bearish_pcr = 1.5
    ce_score = chain_metrics.pcr_score(bearish_pcr, "CE")
    pe_score = chain_metrics.pcr_score(bearish_pcr, "PE")
    check(pe_score > ce_score, "a bearish PCR scores higher for PE than CE")


def test_max_pain_is_the_minimum_payout_strike():
    # Heavy OI concentrated at 24000 on both sides -> pain minimized there.
    chain = make_chain([
        (23800, 1_000, 1_000, 300, 50),
        (24000, 50_000, 50_000, 150, 150),
        (24200, 1_000, 1_000, 50, 300),
    ])
    mp = chain_metrics.max_pain_strike(chain)
    check(mp == 24000, f"max pain lands on the heavy-OI strike (got {mp})")
    check(chain_metrics.max_pain_strike([]) is None, "max_pain_strike returns None on an empty chain")


def test_oi_buildup_labels():
    check(chain_metrics.leg_buildup_label(0.05, 0.05) == "Long Build-up", "price up + OI up = Long Build-up")
    check(chain_metrics.leg_buildup_label(0.05, -0.05) == "Short Build-up", "price down + OI up = Short Build-up")
    check(chain_metrics.leg_buildup_label(-0.05, 0.05) == "Short Covering", "price up + OI down = Short Covering")
    check(chain_metrics.leg_buildup_label(-0.05, -0.05) == "Long Unwinding", "price down + OI down = Long Unwinding")
    check(chain_metrics.leg_buildup_label(0.0, 0.0) == "Neutral", "no meaningful move = Neutral")


def test_oi_concentration_and_strength():
    chain = make_chain([
        (24000, 50_000, 1_000, 150, 150),
        (24100, 1_000, 1_000, 100, 100),
    ])
    top = chain_metrics.oi_concentration(chain, "CE", top_n=1)
    check(top[0][0] == 24000, "oi_concentration picks the heaviest call-OI strike first")
    score_in = chain_metrics.oi_strength_score(50_000, 0.05, top, 24000)
    score_out = chain_metrics.oi_strength_score(1_000, 0.0, top, 24100)
    check(score_in > score_out, "a strike inside the OI concentration + building scores higher")


# ---------------------------------------------------------------------------
# Greeks / IV Engines
# ---------------------------------------------------------------------------

def test_greeks_score_penalizes_high_gamma_theta():
    calm = greeks_iv.greeks_score(gamma=0.0002, theta=-0.5, premium=200, vega=2.0)
    hot = greeks_iv.greeks_score(gamma=0.01, theta=-20, premium=100, vega=2.0)
    check(calm > hot, "low gamma/theta risk scores higher than high gamma/theta risk")
    neutral = greeks_iv.greeks_score(gamma=None, theta=None, premium=100, vega=None)
    check(0.0 <= neutral <= 1.0, "missing Greeks degrade to a neutral score, not a crash")


def test_iv_abnormal_detection():
    # is_iv_abnormal() takes iv in percentage-point scale (e.g. 20.0
    # meaning 20%) -- the scale both angelone.py and upstox.py actually
    # return (see IV_SCALE_VERIFICATION.md) and the scale engine.py
    # actually passes in from leg["iv"]. IV_ABNORMAL_LOW_PCT/HIGH_PCT in
    # config.py are 0-1 fraction scale (0.08/1.20); the function
    # converts internally, so these test inputs must be percentage-point
    # to match real call sites, not fraction-scale.
    check(greeks_iv.is_iv_abnormal(1.0) is True, "IV far below the floor is abnormal")
    check(greeks_iv.is_iv_abnormal(200.0) is True, "IV far above the ceiling is abnormal")
    check(greeks_iv.is_iv_abnormal(20.0) is False, "a normal IV reading is not abnormal")
    check(greeks_iv.is_iv_abnormal(None) is False, "missing IV is 'unknown', not 'abnormal'")


def test_iv_percentile_and_trend():
    engine.reset_state()
    key = "TEST|24000CE"
    for iv in (0.15, 0.18, 0.20, 0.25):
        greeks_iv.record_iv_reading(key, iv)
    check(greeks_iv.iv_percentile(key, 0.25) == 1.0, "the highest-ever reading sits at the 100th percentile")
    check(greeks_iv.iv_trend(key) == "Expansion", "IV rising over the last two readings reads as Expansion")


# ---------------------------------------------------------------------------
# Liquidity / Premium Engines
# ---------------------------------------------------------------------------

def test_premium_score_does_not_penalize_atm_otm_for_being_atm_otm():
    """
    BUGFIX (2026-08-10): premium_score() used to score EVERY ATM/OTM
    strike 0.0 unconditionally -- intrinsic value is always exactly 0
    for ATM/OTM by definition, so pct_time_value was always ~100% and
    that alone tripped the "mostly time value" penalty, regardless of
    whether the quote itself was actually fine. Confirmed live: this
    was the source of "Poor Premium Quality (mostly time value)" on
    every single ATM/OTM OPTIONS_INTEL log line reviewed, and once
    config.OPTIONS_INTELLIGENCE_FILTER_ENABLED was turned on this
    became an unconditional reject of every ATM/OTM strike (scoring.
    options_filter()'s <0.2 hard-hygiene rule) -- a de facto ITM-only
    filter never intended by this system's parallel ITM/ATM/OTM design
    (see scanner.moneyness_offsets()/STRIKE_RANGE).

    Fixed: ATM/OTM (intrinsic <= 0) now scores a neutral 1.0 -- not a
    quality read for these, so not a penalty either. ITM strikes keep
    the real intrinsic/extrinsic check.
    """
    otm_score = liquidity_premium.premium_score(spot_price=24000, strike=25000, direction="CE", premium=20)
    atm_score = liquidity_premium.premium_score(spot_price=24000, strike=24000, direction="CE", premium=120)
    check(otm_score == 1.0, "OTM (intrinsic=0) must not be penalized for its own moneyness")
    check(atm_score == 1.0, "ATM (intrinsic=0) must not be penalized for its own moneyness")


def test_premium_score_still_penalizes_a_genuinely_anomalous_itm_quote():
    """
    Companion to the fix above: an ITM strike (real intrinsic value)
    whose premium is STILL mostly time value despite that is a genuine
    anomaly (e.g. IV skew, stale/bad quote) -- this is the one case
    the intrinsic/extrinsic split is actually a meaningful signal for,
    and it must still be caught.
    """
    intrinsic = liquidity_premium.intrinsic_value(24000, 23900, "CE")  # 100
    anomalous_itm_score = liquidity_premium.premium_score(spot_price=24000, strike=23900, direction="CE", premium=500)
    healthy_itm_score = liquidity_premium.premium_score(spot_price=24000, strike=23000, direction="CE", premium=1050)
    check(intrinsic == 100, "sanity check on the intrinsic value used above")
    check(anomalous_itm_score < 0.2, "ITM premium that's still mostly time value must score low (genuine anomaly)")
    check(healthy_itm_score > anomalous_itm_score, "a healthy, mostly-intrinsic ITM premium scores clearly higher")


def test_intrinsic_extrinsic_split():
    intrinsic = liquidity_premium.intrinsic_value(24000, 23500, "CE")
    check(intrinsic == 500, "CE intrinsic value = spot - strike when ITM")
    extrinsic = liquidity_premium.extrinsic_value(550, intrinsic)
    check(extrinsic == 50, "extrinsic = premium - intrinsic")
    check(liquidity_premium.extrinsic_value(400, intrinsic) == 0, "extrinsic floors at 0, never negative")


# ---------------------------------------------------------------------------
# Strike Selection / Expiry Engines
# ---------------------------------------------------------------------------

def test_strike_depth_labels():
    check(strike_expiry.strike_depth_label("ATM", 0) == "ATM", "ATM stays ATM regardless of offset")
    check(strike_expiry.strike_depth_label("OTM", 1) == "OTM", "offset 1 OTM stays plain OTM")
    check(strike_expiry.strike_depth_label("OTM", 2) == "Deep OTM", "offset >=2 OTM becomes Deep OTM")
    check(strike_expiry.strike_depth_label("ITM", 3) == "Deep ITM", "offset >=2 ITM becomes Deep ITM")


def test_expiry_score_tapers_near_expiry_for_otm():
    far = strike_expiry.expiry_score(10, "OTM")
    near = strike_expiry.expiry_score(1, "OTM")
    check(far > near, "an OTM strike far from expiry scores higher than one on expiry day")


# ---------------------------------------------------------------------------
# Options Momentum Engine
# ---------------------------------------------------------------------------

def test_momentum_reads_direction_of_change():
    momentum.reset_history()
    key = "TEST|24000CE"
    momentum.record_reading(key, premium=100, oi=10_000, volume=5_000, iv=0.18)
    momentum.record_reading(key, premium=120, oi=12_000, volume=7_000, iv=0.20)
    ce_score = momentum.options_momentum_score(key, "CE")
    pe_score = momentum.options_momentum_score(key, "PE")
    check(ce_score > pe_score, "rising premium/OI/volume favors CE momentum over PE momentum")


def test_momentum_neutral_with_one_reading():
    momentum.reset_history()
    key = "TEST|ONLY_ONE"
    momentum.record_reading(key, premium=100, oi=10_000, volume=5_000, iv=0.18)
    check(momentum.premium_momentum_pct(key) is None, "a single reading has nothing to compare against")
    check(momentum.options_momentum_score(key, "CE") == 0.5, "insufficient history scores neutral, not zero")


# ---------------------------------------------------------------------------
# Confirmation / Scoring / Filter Engines
# ---------------------------------------------------------------------------

def test_scoring_weights_sum_and_composite():
    from config import OPTIONS_INTELLIGENCE_WEIGHTS
    check(sum(OPTIONS_INTELLIGENCE_WEIGHTS.values()) == 100, "Options Intelligence weights must sum to 100")
    all_ones = {k: 1.0 for k in OPTIONS_INTELLIGENCE_WEIGHTS}
    check(scoring.options_intelligence_score(all_ones) == 100, "every sub-score at max -> composite of 100")
    all_zeros = {k: 0.0 for k in OPTIONS_INTELLIGENCE_WEIGHTS}
    check(scoring.options_intelligence_score(all_zeros) == 0, "every sub-score at min -> composite of 0")


def test_filter_rejects_poor_liquidity_even_with_high_composite():
    sub_scores = {k: 0.9 for k in ("oi", "pcr", "max_pain", "greeks", "iv", "premium", "strike", "expiry", "momentum")}
    sub_scores["liquidity"] = 0.05  # very poor liquidity despite everything else looking great
    score = scoring.options_intelligence_score(sub_scores)
    accepted, reasons = scoring.options_filter(score, sub_scores)
    check(accepted is False, "poor liquidity rejects even a high composite score")
    check(any("Liquidity" in r for r in reasons), "rejection reasons name liquidity specifically")


def test_confirmation_levels():
    strong = {k: 0.9 for k in ("oi", "pcr", "max_pain", "greeks", "iv", "liquidity", "premium", "momentum")}
    weak = {k: 0.1 for k in ("oi", "pcr", "max_pain", "greeks", "iv", "liquidity", "premium", "momentum")}
    check(scoring.options_confidence(strong) == "Confirmed", "every factor strong -> Confirmed")
    check(scoring.options_confidence(weak) == "Weak", "every factor weak -> Weak")


# ---------------------------------------------------------------------------
# End-to-end: engine.analyze()
# ---------------------------------------------------------------------------

def test_analyze_end_to_end_produces_standard_output_object():
    engine.reset_state()
    chain = make_chain([
        (23900, 8_000, 20_000, 220, 30),
        (24000, 30_000, 30_000, 150, 150),
        (24100, 6_000, 6_000, 80, 240),
    ])
    leg = {
        "instrument_key": "CE|24000", "ltp": 150, "oi": 30_000, "volume": 6_000,
        "bid": 149.5, "ask": 150.5, "delta": 0.5, "gamma": 0.001, "theta": -2.0,
        "vega": 3.0, "iv": 0.18, "prev_oi": 28_000,
    }
    result = engine.analyze(
        symbol="NIFTY", direction="CE", moneyness="ATM", offset_index=0,
        strike=24000, leg=leg, chain=chain, spot_price=24010, expiry="2026-08-06",
        oi_history={}, oi_delta_pct=0.02,
    )
    required_keys = [
        "oi_score", "pcr_score", "max_pain_score", "greeks_score", "iv_score",
        "liquidity_score", "premium_score", "strike_recommendation", "expiry_score",
        "options_intelligence_score", "confidence", "warning_list", "timestamp",
    ]
    for key in required_keys:
        check(key in result, f"Standard Output Object includes '{key}'")
    check(0 <= result["options_intelligence_score"] <= 100, "composite score is within 0-100")
    check(result["strike_recommendation"] == "ATM", "ATM strike is labeled ATM in the output object")


def test_analyze_disabled_short_circuits_cleanly():
    import config
    original = config.OPTIONS_INTELLIGENCE_ENABLED
    config.OPTIONS_INTELLIGENCE_ENABLED = False
    try:
        # Re-import the flag inside engine.py's module namespace, since
        # it was imported by value at module load time -- patch it
        # directly on the engine module for this test, then restore.
        original_engine_flag = engine.OPTIONS_INTELLIGENCE_ENABLED
        engine.OPTIONS_INTELLIGENCE_ENABLED = False
        result = engine.analyze(
            symbol="NIFTY", direction="CE", moneyness="ATM", offset_index=0,
            strike=24000, leg={}, chain=[], spot_price=24000, expiry="2026-08-06",
            oi_history={},
        )
        check(result == {"enabled": False, "timestamp": result["timestamp"]}, "disabled engine returns a minimal, harmless object")
    finally:
        config.OPTIONS_INTELLIGENCE_ENABLED = original
        engine.OPTIONS_INTELLIGENCE_ENABLED = original_engine_flag


def test_analyze_handles_missing_greeks_gracefully():
    engine.reset_state()
    chain = make_chain([(24000, 20_000, 20_000, 150, 150)])
    # Angel-One-style leg missing gamma/theta/vega/iv entirely.
    leg = {
        "instrument_key": "CE|24000", "ltp": 150, "oi": 20_000, "volume": 5_000,
        "bid": 149, "ask": 151, "delta": 0.5, "gamma": None, "theta": None,
        "vega": None, "iv": None, "prev_oi": None,
    }
    result = engine.analyze(
        symbol="NIFTY", direction="CE", moneyness="ATM", offset_index=0,
        strike=24000, leg=leg, chain=chain, spot_price=24000, expiry="2026-08-06",
        oi_history={}, oi_delta_pct=0.0,
    )
    check(result["enabled"] is True, "missing Greeks/IV does not disable the whole engine")
    check(0 <= result["options_intelligence_score"] <= 100, "still produces a valid composite score")


if __name__ == "__main__":
    tests = [obj for name, obj in list(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
    print(f"\n{len(tests)} test functions passed.")
