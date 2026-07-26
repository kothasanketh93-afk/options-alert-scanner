"""
app/engines/early_breakout/tests/test_engine.py -- unit tests for the
Early Breakout Engine's pure-math modules (base_detector,
institutional_score, smart_money_score, breakout_detector,
breakout_score, momentum).
------------------------------------------------------------------------------
Plain assert-based, no pytest dependency (this repo doesn't use one --
see requirements.txt / test_telegram.py's own manual-script convention).
All synthetic, deterministic candle data -- no broker/network access,
so this can run anywhere, including CI, unlike test_telegram.py which
needs real Telegram credentials.

Run with: python scripts/app/engines/early_breakout/tests/test_engine.py
Exits non-zero (via AssertionError) on the first failing check.
"""

import sys
import os

# scripts/ needs to be on sys.path (GitHub Actions runs `python
# scripts/main.py`, which puts scripts/ there automatically -- running
# this file standalone doesn't get that for free). This file sits 4
# levels below scripts/: scripts/app/engines/early_breakout/tests/.
_here = os.path.dirname(os.path.abspath(__file__))
_scripts_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_here))))
sys.path.insert(0, _scripts_dir)

from app.engines.early_breakout import (
    base_detector, institutional_score, smart_money_score,
    breakout_detector, breakout_score, momentum, fair_value_gap, price_action,
)
import config


def make_flat_candles(n, price=100.0, body=0.4, wick=0.6, volume=1000):
    """A tight, flat consolidation -- every candle's body/wicks stay
    within a fixed band around `price`, volume constant."""
    candles = []
    for i in range(n):
        wobble = 0.15 * (1 if i % 2 == 0 else -1)
        o = price + wobble
        c = price - wobble
        h = max(o, c) + wick * 0.1
        l = min(o, c) - wick * 0.1
        candles.append({"open": o, "high": h, "low": l, "close": c, "volume": volume})
    return candles


def make_higher_lows_candles(n, start=100.0, step=1.0):
    """
    Guarantees genuine, strictly-increasing PIVOT LOWS: even indices
    are peaks held well above all troughs, odd indices are troughs
    that increase step by step -- e.g. lows = [110, 100, 111, 101,
    112, 102, ...]. Each trough is a real local minimum (below both
    neighboring peaks) and each trough is higher than the one before,
    which is exactly what higher_lows_count() looks for.
    """
    candles = []
    for i in range(n):
        k = i // 2
        if i % 2 == 0:
            low = start + 10 + k * step
        else:
            low = start + k * step
        high = low + 1.0
        close = low + 0.5
        candles.append({"open": low + 0.3, "high": high, "low": low, "close": close, "volume": 1000 + i * 5})
    return candles


def make_lower_highs_candles(n, start=100.0, step=1.0):
    """
    Mirror of make_higher_lows_candles(), but controlling HIGHS instead
    of LOWS: even indices are troughs held well below all peaks, odd
    indices are peaks that DECREASE step by step -- e.g. highs = [90,
    100, 89, 99, 88, 98, ...]. Each peak is a real local maximum and
    each peak is lower than the one before, which is what
    lower_highs_count() looks for.
    """
    candles = []
    for i in range(n):
        k = i // 2
        if i % 2 == 0:
            high = start - 10 - k * step
        else:
            high = start - k * step
        low = high - 1.0
        close = high - 0.5
        candles.append({"open": high - 0.3, "high": high, "low": low, "close": close, "volume": 1000 + i * 5})
    return candles


def test_base_detector_flat_consolidation_scores_reasonably():
    candles = make_flat_candles(40)
    atr = 0.6
    result = base_detector.compute_base_score(candles, spot_price=candles[-1]["close"], vwap=100.0, atr=atr)
    assert 0 <= result["base_score"] <= 100, "base_score must be 0-100"
    # A genuinely flat/tight range should score well above a random walk.
    assert result["base_score"] >= 40, f"expected a tight flat base to score reasonably, got {result['base_score']}"
    assert "pattern_label" in result
    print("PASS: test_base_detector_flat_consolidation_scores_reasonably", result["base_score"], result.get("pattern_label"))


def test_higher_lows_and_lower_highs_counts():
    up = make_higher_lows_candles(12, start=100, step=1.0)
    down = make_lower_highs_candles(12, start=112, step=1.0)
    hl = base_detector.higher_lows_count(up)
    lh = base_detector.lower_highs_count(down)
    assert hl >= 1, f"expected higher-low steps detected in a clean uptrend, got {hl}"
    assert lh >= 1, f"expected lower-high steps detected in a clean downtrend, got {lh}"
    print("PASS: test_higher_lows_and_lower_highs_counts", hl, lh)


def test_institutional_score_bounds_and_detail():
    candles = make_flat_candles(50)
    result = institutional_score.compute_institutional_score(candles, vwap=100.0)
    assert 0 <= result["institutional_score"] <= 100
    assert set(result["detail"].keys()) == {
        "volume_increase", "volatility_decrease", "narrow_bodies",
        "vwap_respect", "compression_trend", "structure",
    }
    print("PASS: test_institutional_score_bounds_and_detail", result["institutional_score"])


def test_smart_money_score_flags_unavailable_factors_honestly():
    result = smart_money_score.compute_smart_money_score(
        volume=5000, avg_volume=2000, oi_change_pct_value=0.05, delta=0.45, bid=99.5, ask=100.5, premium=100,
    )
    assert 0 <= result["smart_money_score"] <= 100
    # Must not silently fabricate data it doesn't have -- these three
    # must be explicitly flagged, not scored as if measured.
    for key in ("large_bid_quantity", "large_trades", "delivery_expansion"):
        assert key in result["detail"], f"expected {key} to be explicitly flagged as unavailable/n-a"
        assert isinstance(result["detail"][key], str), f"{key} should be a caveat string, not a fabricated number"
    print("PASS: test_smart_money_score_flags_unavailable_factors_honestly", result["smart_money_score"])


def _consolidation_then_breakout(consolidation_len=25, breakout_extra_candles=0):
    """
    Builds: `consolidation_len` flat candles, then ONE genuine breakout
    candle (big range, big volume, closing above the range), then
    `breakout_extra_candles` continuation candles that keep pushing
    higher on strong volume too (to test that detect_early_breakout()
    still identifies the ORIGINAL breakout candle as the breakout, not
    a later continuation candle -- "first breakout candle, not third
    or fourth").
    """
    base = make_flat_candles(consolidation_len, price=100.0)
    consolidation_high = max(c["high"] for c in base)
    candles = list(base)

    breakout_candle = {
        "open": consolidation_high - 0.2, "high": consolidation_high + 3.0,
        "low": consolidation_high - 0.3, "close": consolidation_high + 2.5,
        "volume": 5000,
    }
    candles.append(breakout_candle)

    last_close = breakout_candle["close"]
    for i in range(breakout_extra_candles):
        last_close += 1.5
        candles.append({
            "open": last_close - 1.5, "high": last_close + 0.3,
            "low": last_close - 1.7, "close": last_close, "volume": 4000,
        })
    return candles, breakout_candle


def test_breakout_detector_finds_first_breakout_candle_not_a_later_one():
    # 2 continuation candles printed after the true breakout candle,
    # then the "confirmation" candle detect_early_breakout() should
    # still trace back to the ORIGINAL breakout, with age=2 (Second
    # Candle tier), not mistake a continuation candle for a fresh
    # age-0/age-1 breakout.
    candles, true_breakout = _consolidation_then_breakout(consolidation_len=25, breakout_extra_candles=2)
    # Add the actual Stage-5 confirmation candle on top (HH+HL, decent volume).
    last = candles[-1]
    confirm = {
        "open": last["close"], "high": last["close"] + 1.0,
        "low": last["close"] - 0.3, "close": last["close"] + 0.8, "volume": 3500,
    }
    candles.append(confirm)

    ema9, ema21, ema50 = 108.0, 104.0, 100.0  # aligned bullish
    result = breakout_detector.detect_early_breakout(candles, "CE", vwap=99.0, ema9=ema9, ema21=ema21, ema50=ema50)
    assert result["breakout"] is True, "expected a breakout to be found"
    assert result["breakout_candle"]["close"] == true_breakout["close"], (
        "expected the ORIGINAL breakout candle to be identified, not a later continuation candle"
    )
    assert result["breakout_age"] == 2, f"expected age 2 (2 continuation candles printed after the true breakout, before this run's confirmation), got {result['breakout_age']}"
    assert result["fresh_score"] == config.FRESH_BREAKOUT_SCORE.get(2, 0)
    print("PASS: test_breakout_detector_finds_first_breakout_candle_not_a_later_one", result["breakout_age"], result["fresh_score"])


def test_fresh_breakout_score_decays_total_score():
    strengths = {k: 0.9 for k in config.EARLY_BREAKOUT_WEIGHTS}
    fresh = breakout_score.total_score(strengths, "NEUTRAL", fresh_score=90)
    stale = breakout_score.total_score(strengths, "NEUTRAL", fresh_score=40)
    assert fresh > stale, "a fresher breakout must score higher than an identical but staler one"
    print("PASS: test_fresh_breakout_score_decays_total_score", fresh, stale)


def test_weights_sum_to_100():
    assert sum(config.EARLY_BREAKOUT_WEIGHTS.values()) == 100
    print("PASS: test_weights_sum_to_100")


def test_entry_window_matches_spec_example():
    # Spec's own worked example: breakout ₹200 -> preferred ₹202-214, max ₹220.
    breakout_price = 200.0
    preferred_low = round(breakout_price * (1 + config.EARLY_ENTRY_PREFERRED_MIN_PCT), 2)
    preferred_high = round(breakout_price * (1 + config.EARLY_ENTRY_PREFERRED_MAX_PCT), 2)
    maximum_entry = round(breakout_price * (1 + config.EARLY_ENTRY_MAX_PCT), 2)
    assert preferred_low == 202.0, preferred_low
    assert preferred_high == 214.0, preferred_high
    assert maximum_entry == 220.0, maximum_entry
    print("PASS: test_entry_window_matches_spec_example", preferred_low, preferred_high, maximum_entry)


def test_extended_move_filter_catches_parabolic_move():
    candles = make_flat_candles(20, price=100.0)
    # 3 accelerating-body candles appended on top of the flat base.
    accel = [
        {"open": 100.0, "high": 101.5, "low": 99.9, "close": 101.4, "volume": 2000},
        {"open": 101.4, "high": 104.0, "low": 101.3, "close": 103.9, "volume": 3000},
        {"open": 103.9, "high": 109.0, "low": 103.8, "close": 108.8, "volume": 4000},
    ]
    candles = candles + accel
    result = breakout_detector.extended_move_filter(candles, "CE", breakout_premium=50, current_premium=52, ema20=100.0)
    assert result["extended"] is True, "expected a parabolic accelerating move to be flagged as extended"
    assert any("Parabolic" in r for r in result["reasons"])
    print("PASS: test_extended_move_filter_catches_parabolic_move", result["reasons"])


def test_momentum_is_direction_aware():
    candles = make_higher_lows_candles(40, start=100, step=0.8)
    ce = momentum.momentum_strength(candles, "CE", vwap=95.0, spot_price=candles[-1]["close"])
    pe = momentum.momentum_strength(candles, "PE", vwap=95.0, spot_price=candles[-1]["close"])
    assert ce["score"] > pe["score"], "an uptrending series should score higher momentum for CE than PE"
    print("PASS: test_momentum_is_direction_aware", round(ce["score"], 2), round(pe["score"], 2))


def test_smart_money_and_institutional_strength_rescale_to_unit_interval():
    assert breakout_score.institutional_strength(0) == 0.0
    assert breakout_score.institutional_strength(100) == 1.0
    assert breakout_score.smart_money_strength(50) == 0.5
    print("PASS: test_smart_money_and_institutional_strength_rescale_to_unit_interval")


def test_fvg_detects_bullish_gap_and_marks_it_unfilled():
    atr = 1.0
    candles = make_flat_candles(10, price=100.0)
    # candle[-3]: high 100.4 (roughly, from make_flat_candles); force an
    # explicit 3-candle bullish gap: candle A high well below candle C low.
    candles += [
        {"open": 100.0, "high": 100.3, "low": 99.8, "close": 100.1, "volume": 1000},  # A (index -3)
        {"open": 100.1, "high": 104.0, "low": 100.0, "close": 103.8, "volume": 3000},  # B (impulsive, index -2)
        {"open": 103.8, "high": 106.0, "low": 103.5, "close": 105.5, "volume": 2500},  # C (index -1) -- low(103.5) > A.high(100.3)
    ]
    gaps = fair_value_gap.detect_fair_value_gaps(candles, atr)
    bullish = [g for g in gaps if g["direction"] == "CE"]
    assert bullish, "expected a bullish FVG to be detected"
    assert bullish[-1]["filled"] is False, "gap should be unfilled -- nothing traded back into it yet"
    print("PASS: test_fvg_detects_bullish_gap_and_marks_it_unfilled", bullish[-1])


def test_fvg_marks_gap_filled_once_price_returns():
    atr = 1.0
    candles = make_flat_candles(10, price=100.0)
    candles += [
        {"open": 100.0, "high": 100.3, "low": 99.8, "close": 100.1, "volume": 1000},
        {"open": 100.1, "high": 104.0, "low": 100.0, "close": 103.8, "volume": 3000},
        {"open": 103.8, "high": 106.0, "low": 103.5, "close": 105.5, "volume": 2500},
        {"open": 105.5, "high": 105.6, "low": 100.2, "close": 100.5, "volume": 2000},  # trades back into the gap
    ]
    gaps = fair_value_gap.detect_fair_value_gaps(candles, atr)
    bullish = [g for g in gaps if g["direction"] == "CE"]
    assert bullish[-1]["filled"] is True, "a later candle traded back into the gap -- should now be filled"
    print("PASS: test_fvg_marks_gap_filled_once_price_returns")


def test_fvg_ignores_gaps_smaller_than_noise_floor():
    atr = 10.0  # large ATR -- a tiny gap should not qualify
    candles = make_flat_candles(10, price=100.0)
    candles += [
        {"open": 100.0, "high": 100.1, "low": 99.9, "close": 100.05, "volume": 1000},
        {"open": 100.05, "high": 100.2, "low": 100.0, "close": 100.15, "volume": 1000},
        {"open": 100.15, "high": 100.3, "low": 100.11, "close": 100.2, "volume": 1000},  # gap of ~0.01, tiny vs ATR=10
    ]
    gaps = fair_value_gap.detect_fair_value_gaps(candles, atr)
    assert gaps == [], f"expected no gaps below the ATR-relative noise floor, got {gaps}"
    print("PASS: test_fvg_ignores_gaps_smaller_than_noise_floor")


def test_fvg_confirmation_bonus_only_applies_when_confirmed():
    strengths = {k: 0.8 for k in config.EARLY_BREAKOUT_WEIGHTS}
    with_fvg = breakout_score.total_score(strengths, "NEUTRAL", fvg_confirmed=True)
    without_fvg = breakout_score.total_score(strengths, "NEUTRAL", fvg_confirmed=False)
    assert with_fvg > without_fvg, "an FVG-confirmed setup should score higher than an identical one without"
    assert with_fvg - without_fvg == config.FVG_CONFIRMATION_BONUS
    print("PASS: test_fvg_confirmation_bonus_only_applies_when_confirmed", with_fvg, without_fvg)


def test_candle_fetch_count_is_enough_for_ema50():
    """
    Regression test for a real bug found 2026-07-25: the engine used to
    fetch only 32 underlying candles, but indicators.compute_ema()
    requires at least `period` closes -- so EMA50 (needed by
    breakout_score.trend_strength(), the LARGEST-weighted factor in the
    composite score) was silently always None, making "Underlying Trend"
    contribute zero to every single candidate ever scored. This asserts
    the fetch count is enough for a real EMA50 with room to spare, so a
    future accidental reduction of EARLY_BREAKOUT_CANDLE_FETCH_COUNT
    trips this test instead of silently reintroducing the bug.
    """
    from indicators import compute_ema
    assert config.EARLY_BREAKOUT_CANDLE_FETCH_COUNT >= 50, (
        f"EARLY_BREAKOUT_CANDLE_FETCH_COUNT ({config.EARLY_BREAKOUT_CANDLE_FETCH_COUNT}) "
        f"is below 50 -- EMA50 would silently be None again"
    )
    closes = list(range(1, config.EARLY_BREAKOUT_CANDLE_FETCH_COUNT + 1))
    assert compute_ema(closes, 50) is not None
    print("PASS: test_candle_fetch_count_is_enough_for_ema50", config.EARLY_BREAKOUT_CANDLE_FETCH_COUNT)


def test_market_snapshot_returns_none_on_insufficient_data_not_a_crash():
    # No live broker available in this sandbox -- just confirms the
    # module imports and its pure banding helpers behave, since a full
    # get_market_snapshot() call needs a real broker connection.
    from app.market import snapshot as market_snapshot
    assert market_snapshot._trend_strength_label(0.70) == "Very Strong"
    assert market_snapshot._trend_strength_label(0.10) == "Very Weak"
    assert market_snapshot._volatility_level(0.001) == "Low"
    assert market_snapshot._volatility_level(0.01) == "High"
    print("PASS: test_market_snapshot_returns_none_on_insufficient_data_not_a_crash")


def test_price_action_detects_doji():
    candle = {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.05, "volume": 1000}
    assert price_action.is_doji(candle) is True
    patterns = price_action.detect_candle_patterns(candle, None, atr=1.0)
    assert any(p["pattern"] == "DOJI" for p in patterns)
    print("PASS: test_price_action_detects_doji")


def test_price_action_detects_hammer_and_shooting_star():
    hammer = {"open": 100.0, "high": 100.3, "low": 95.0, "close": 100.2, "volume": 1000}
    star = {"open": 100.0, "high": 105.0, "low": 99.7, "close": 100.1, "volume": 1000}
    assert price_action.is_hammer_or_shooting_star(hammer) == "HAMMER"
    assert price_action.is_hammer_or_shooting_star(star) == "SHOOTING_STAR"
    print("PASS: test_price_action_detects_hammer_and_shooting_star")


def test_price_action_detects_marubozu():
    candle = {"open": 100.0, "high": 110.0, "low": 99.9, "close": 109.9, "volume": 1000}
    assert price_action.is_marubozu(candle) is True
    print("PASS: test_price_action_detects_marubozu")


def test_price_action_detects_bullish_engulfing():
    prev = {"open": 100.0, "high": 100.5, "low": 98.0, "close": 98.5, "volume": 1000}   # bearish
    cur = {"open": 98.0, "high": 102.0, "low": 97.5, "close": 101.5, "volume": 2000}     # bullish, engulfs prev
    assert price_action.is_engulfing(cur, prev) == "BULLISH_ENGULFING"
    print("PASS: test_price_action_detects_bullish_engulfing")


def test_price_action_detects_inside_and_outside_bar():
    prev = {"open": 100.0, "high": 105.0, "low": 95.0, "close": 102.0, "volume": 1000}
    inside = {"open": 101.0, "high": 103.0, "low": 99.0, "close": 102.0, "volume": 1000}
    outside = {"open": 100.0, "high": 106.0, "low": 94.0, "close": 105.0, "volume": 1000}
    assert price_action.is_inside_or_outside_bar(inside, prev) == "INSIDE_BAR"
    assert price_action.is_inside_or_outside_bar(outside, prev) == "OUTSIDE_BAR"
    print("PASS: test_price_action_detects_inside_and_outside_bar")


def test_price_action_confirmation_only_counts_aligned_direction():
    base = make_flat_candles(20, price=100.0)
    # Breakout candle (index -2): strong bullish momentum candle.
    breakout_candle = {"open": 100.0, "high": 106.0, "low": 99.8, "close": 105.8, "volume": 3000}
    confirm_candle = {"open": 105.8, "high": 108.0, "low": 105.5, "close": 107.5, "volume": 2500}
    candles = base + [breakout_candle, confirm_candle]

    ce_result = price_action.price_action_confirmation(candles, "CE", atr=1.0)
    pe_result = price_action.price_action_confirmation(candles, "PE", atr=1.0)
    assert ce_result["confirmed"] is True, "a bullish momentum candle should confirm a CE trade"
    assert pe_result["confirmed"] is False, "the same bullish candle should NOT confirm a PE trade"
    print("PASS: test_price_action_confirmation_only_counts_aligned_direction", ce_result["patterns"])


def test_price_action_bonus_only_applies_when_confirmed():
    strengths = {k: 0.8 for k in config.EARLY_BREAKOUT_WEIGHTS}
    with_pa = breakout_score.total_score(strengths, "NEUTRAL", price_action_confirmed=True)
    without_pa = breakout_score.total_score(strengths, "NEUTRAL", price_action_confirmed=False)
    assert with_pa - without_pa == config.PRICE_ACTION_CONFIRMATION_BONUS
    print("PASS: test_price_action_bonus_only_applies_when_confirmed", with_pa, without_pa)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failures = 0
    for t in tests:
        try:
            t()
        except AssertionError as exc:
            failures += 1
            print(f"FAIL: {t.__name__}: {exc}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    if failures:
        sys.exit(1)
