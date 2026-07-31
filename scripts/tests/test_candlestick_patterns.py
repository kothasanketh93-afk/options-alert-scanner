"""
tests/test_candlestick_patterns.py
------------------------------------------------------------------------------
Plain assert-based tests, same convention as tests/test_atr_warmup.py
(no pytest dependency). candlestick_patterns.py only imports from
config.py (pure constants, no broker/network deps), so no stubbing is
needed to import the real module here.

Covers:
  - Each of the 13 pattern detectors, on a small hand-built candle that
    should (and one that should not) trigger it.
  - detect() returning multiple matches when applicable.
  - pattern_alignment_score() direction-awareness and its None-when-
    no-directional-pattern contract.
  - analyze()'s always-a-dict, never-raises contract, including on
    too-few/empty candle lists.

Run with: python scripts/tests/test_candlestick_patterns.py
Exits non-zero (via AssertionError) on the first failing check.
"""

import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
_scripts_dir = os.path.dirname(_here)
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)

import candlestick_patterns as cp  # noqa: E402


def c(o, h, l, close):
    return {"open": o, "high": h, "low": l, "close": close, "volume": 1000}


def run():
    # --- Doji: open ~= close, real range ---
    assert cp.is_doji([c(100, 105, 95, 100.2)]) is True
    assert cp.is_doji([c(100, 110, 90, 108)]) is False  # big body, not a doji

    # --- Spinning Top: small body, both wicks >= body ---
    assert cp.is_spinning_top([c(100, 106, 94, 102)]) is True
    assert cp.is_doji([c(100, 106, 94, 102)]) is False  # body too big to be a doji
    assert cp.is_spinning_top([c(100, 101, 99, 100.05)]) is False  # too small -- that's a doji

    # --- Marubozu: negligible wicks, full body ---
    assert cp.marubozu_direction([c(100, 120, 100, 120)]) == "bullish"
    assert cp.marubozu_direction([c(120, 120, 100, 100)]) == "bearish"
    assert cp.marubozu_direction([c(100, 115, 95, 110)]) is None  # real wicks present

    # --- Hammer: small body near top, long lower wick, small upper wick ---
    assert cp.is_hammer([c(100, 101, 80, 100.5)]) is True
    assert cp.is_hammer([c(100, 120, 99, 119)]) is False  # marubozu-ish, not a hammer

    # --- Shooting Star: mirror of hammer ---
    assert cp.is_shooting_star([c(100, 120, 99.5, 100.5)]) is True
    assert cp.is_shooting_star([c(100, 101, 80, 100.5)]) is False  # that's the hammer shape, not this

    # --- Bullish / Bearish Engulfing ---
    bearish_then_bullish = [c(100, 101, 90, 91), c(90, 111, 89, 110)]
    assert cp.is_bullish_engulfing(bearish_then_bullish) is True
    assert cp.is_bearish_engulfing(bearish_then_bullish) is False

    bullish_then_bearish = [c(90, 111, 89, 110), c(111, 112, 89, 90)]
    assert cp.is_bearish_engulfing(bullish_then_bearish) is True
    assert cp.is_bullish_engulfing(bullish_then_bearish) is False

    # --- Inside Bar / Outside Bar ---
    outer_then_inner = [c(100, 120, 80, 110), c(105, 115, 90, 108)]
    assert cp.is_inside_bar(outer_then_inner) is True
    assert cp.is_outside_bar(outer_then_inner) is False

    inner_then_outer = [c(105, 115, 90, 108), c(100, 120, 80, 110)]
    assert cp.is_outside_bar(inner_then_outer) is True
    assert cp.is_inside_bar(inner_then_outer) is False

    # --- Morning Star / Evening Star ---
    morning = [c(110, 112, 90, 91), c(89, 91, 85, 88), c(89, 115, 88, 112)]
    assert cp.is_morning_star(morning) is True
    assert cp.is_evening_star(morning) is False

    evening = [c(90, 112, 89, 111), c(112, 114, 108, 110), c(111, 113, 85, 88)]
    assert cp.is_evening_star(evening) is True
    assert cp.is_morning_star(evening) is False

    # --- Three White Soldiers / Three Black Crows ---
    soldiers = [c(100, 111, 99, 110), c(105, 122, 104, 121), c(115, 133, 114, 132)]
    assert cp.is_three_white_soldiers(soldiers) is True
    assert cp.is_three_black_crows(soldiers) is False

    crows = [c(132, 133, 121, 122), c(128, 129, 112, 113), c(120, 121, 99, 100)]
    assert cp.is_three_black_crows(crows) is True
    assert cp.is_three_white_soldiers(crows) is False

    # --- detect(): multiple matches allowed, empty/short input is safe ---
    assert cp.detect([]) == []
    assert cp.detect([c(100, 101, 99, 100.2)]) != []  # the doji candle above
    names = {d["pattern"] for d in cp.detect(bearish_then_bullish)}
    assert "Bullish Engulfing" in names

    # --- pattern_alignment_score(): direction-aware, None when nothing directional ---
    score_ce = cp.pattern_alignment_score(bearish_then_bullish, "CE")
    score_pe = cp.pattern_alignment_score(bearish_then_bullish, "PE")
    assert score_ce is not None and score_pe is not None
    assert score_ce > 0.5 > score_pe  # bullish engulfing favors CE, penalizes PE
    assert round(score_ce + score_pe, 6) == 1.0  # symmetric by construction

    doji_only = [c(100, 105, 95, 100.2)]
    assert cp.pattern_alignment_score(doji_only, "CE") is None  # neutral-only -> no directional read
    assert cp.pattern_alignment_score([], "CE") is None

    # --- analyze(): always a dict, never raises, sane keys ---
    result = cp.analyze(bearish_then_bullish, "CE")
    assert result["enabled"] is True
    assert "Bullish Engulfing" in result["patterns_detected"]
    assert result["dominant_pattern"] is not None
    assert result["alignment_score"] is not None
    assert 0 <= result["alignment_score_pct"] <= 100

    empty_result = cp.analyze([], "CE")
    assert empty_result["enabled"] is True
    assert empty_result["patterns_detected"] == []
    assert empty_result["alignment_score"] is None
    assert empty_result["alignment_score_pct"] is None

    print("test_candlestick_patterns: all checks passed")


if __name__ == "__main__":
    run()
