"""
app/engines/replay_analytics/tests/test_replay_analytics.py
------------------------------------------------------------------------------
Plain assert-based tests, no pytest dependency -- same convention as
app/engines/decision_engine/tests/test_decision_engine.py. All
synthetic, deterministic candle data, no broker/network access.

Covers: Replay Engine resampling (unit + regression), Signal Replay
across the T1/T2/T3/SL/NOT_ENTERED/EOD_CLOSED/AMBIGUOUS lifecycle
(replay tests), Outcome Classifier, Performance Analytics arithmetic
(analytics validation), Missed Opportunity / False Signal detectors,
Learning Engine grouping, Scoring Calibration, Health Engine, Trade
Journal entry-building, and a rough replay-speed smoke test
(performance test).

Run with:
    python scripts/app/engines/replay_analytics/tests/test_replay_analytics.py
Exits non-zero (via AssertionError) on the first failing check.
"""

import sys
import os
import time

_here = os.path.dirname(os.path.abspath(__file__))
_scripts_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_here))))
sys.path.insert(0, _scripts_dir)

from app.engines.replay_analytics import (
    replay_engine, signal_replay, outcome_classifier, performance_analytics,
    missed_opportunity, false_signal, learning_engine, scoring_calibration,
    health_engine, trade_journal,
)


def _candle(ts, o, h, l, c):
    return {"timestamp": ts, "open": o, "high": h, "low": l, "close": c}


def _one_min_series(start_hhmm, closes, base=100.0, wiggle=0.5):
    """Builds a simple deterministic 1-min candle series from a list
    of closes -- high/low derived from a fixed wiggle around open/close."""
    hh, mm = start_hhmm
    candles = []
    prev_close = base
    for i, close in enumerate(closes):
        minute_total = hh * 60 + mm + i
        ts = f"2026-07-13T{minute_total // 60:02d}:{minute_total % 60:02d}:00+05:30"
        o = prev_close
        h = max(o, close) + wiggle
        l = min(o, close) - wiggle
        candles.append(_candle(ts, o, h, l, close))
        prev_close = close
    return candles


# ---------------------------------------------------------------------------
# Replay Engine: resampling (unit + regression)
# ---------------------------------------------------------------------------

def test_resample_5min_groups_correctly():
    candles = _one_min_series((9, 15), [100, 101, 102, 103, 104, 105, 106, 107, 108, 109])
    out = replay_engine.resample_candles(candles, "5min")
    # 9:15-9:19 -> bucket 1, 9:20-9:24 -> bucket 2
    assert len(out) == 2, f"expected 2 buckets, got {len(out)}"
    assert out[0]["open"] == candles[0]["open"]
    assert out[0]["close"] == candles[4]["close"]
    assert out[0]["high"] == max(c["high"] for c in candles[0:5])
    assert out[0]["low"] == min(c["low"] for c in candles[0:5])
    assert out[1]["close"] == candles[9]["close"]


def test_resample_1min_passthrough():
    candles = _one_min_series((9, 15), [100, 101, 102])
    out = replay_engine.resample_candles(candles, "1min")
    assert out == candles


def test_resample_empty_input():
    assert replay_engine.resample_candles([], "5min") == []


def test_resample_all_timeframes_covers_config_list():
    from config import REPLAY_TIMEFRAMES
    candles = _one_min_series((9, 15), [100] * 90)
    engine = replay_engine.ReplayEngine()
    result = engine.resample_all_timeframes(candles)
    assert set(result.keys()) == set(REPLAY_TIMEFRAMES)
    for tf, out in result.items():
        assert isinstance(out, list) and len(out) > 0, f"{tf} produced no candles"


# ---------------------------------------------------------------------------
# Signal Replay (the core "replay" tests)
# ---------------------------------------------------------------------------

def _trade(entry_low=100, entry_high=102, stop_loss=95, t1=110, t2=120, t3=None, direction="CE"):
    return {"symbol": "NIFTY", "direction": direction, "entry_low": entry_low,
            "entry_high": entry_high, "stop_loss": stop_loss,
            "target_1": t1, "target_2": t2, "target_3": t3}


def test_signal_replay_hits_target_1_only():
    trade = _trade(t2=None)
    candles = _one_min_series((9, 30), [101, 103, 108, 111, 112], base=100, wiggle=0.1)
    result = signal_replay.replay_signal(trade, candles)
    assert result["entered"] is True
    assert result["exit_reason"] == "T1_HIT"
    assert result["time_to_target"] is not None


def test_signal_replay_hits_stop_loss():
    trade = _trade()
    candles = _one_min_series((9, 30), [101, 99, 96, 93], base=100, wiggle=0.1)
    result = signal_replay.replay_signal(trade, candles)
    assert result["entered"] is True
    assert result["exit_reason"] == "SL_HIT"
    assert result["time_to_stop_loss"] is not None


def test_signal_replay_target_3_leg():
    trade = _trade(t1=105, t2=112, t3=125)
    candles = _one_min_series((9, 30), [101, 106, 113, 126], base=100, wiggle=0.1)
    result = signal_replay.replay_signal(trade, candles)
    assert result["exit_reason"] == "T3_HIT"


def test_signal_replay_not_entered():
    trade = _trade(entry_low=200, entry_high=205)
    candles = _one_min_series((9, 30), [101, 102, 103], base=100, wiggle=0.1)
    # push past market close so NOT_ENTERED can resolve
    candles.append(_candle("2026-07-13T15:35:00+05:30", 103, 104, 102, 103))
    result = signal_replay.replay_signal(trade, candles)
    assert result["exit_reason"] == "NOT_ENTERED"
    assert result["entered"] is False


def test_signal_replay_eod_closed():
    trade = _trade(entry_low=100, entry_high=102, stop_loss=90, t1=130, t2=150)
    candles = _one_min_series((9, 30), [101, 103, 105])
    candles.append(_candle("2026-07-13T15:35:00+05:30", 105, 106, 104, 105))
    result = signal_replay.replay_signal(trade, candles)
    assert result["exit_reason"] == "EOD_CLOSED"


def test_signal_replay_ambiguous_same_candle():
    trade = _trade(entry_low=100, entry_high=102, stop_loss=95, t1=110)
    candles = _one_min_series((9, 30), [101])
    # one candle that spans both SL and T1
    candles.append(_candle("2026-07-13T09:32:00+05:30", 101, 112, 94, 105))
    result = signal_replay.replay_signal(trade, candles)
    assert result["exit_reason"] == "AMBIGUOUS"


def test_signal_replay_max_profit_and_drawdown():
    trade = _trade(t1=200, t2=None)  # target far away so it never exits early
    candles = _one_min_series((9, 30), [101, 108, 97, 103], base=100, wiggle=0.1)
    result = signal_replay.replay_signal(trade, candles)
    assert result["entered"] is True
    assert result["max_profit_pct"] > 0
    assert result["max_drawdown_pct"] >= 0


def test_signal_replay_no_candles():
    trade = _trade()
    result = signal_replay.replay_signal(trade, [])
    assert result["exit_reason"] == "NO_CANDLES"
    assert result["entered"] is False


# ---------------------------------------------------------------------------
# Outcome Classifier
# ---------------------------------------------------------------------------

def test_outcome_classifier_win_on_target():
    trade = _trade()
    rr = {"exit_reason": "T1_HIT", "entry": 101, "exit_price": 110}
    assert outcome_classifier.classify_outcome(trade, rr) == "WIN"


def test_outcome_classifier_loss_on_sl():
    trade = _trade()
    rr = {"exit_reason": "SL_HIT", "entry": 101, "exit_price": 95}
    assert outcome_classifier.classify_outcome(trade, rr) == "LOSS"


def test_outcome_classifier_no_entry():
    trade = _trade()
    rr = {"exit_reason": "NOT_ENTERED"}
    assert outcome_classifier.classify_outcome(trade, rr) == "NO_ENTRY"


def test_outcome_classifier_invalid_signal_missing_fields():
    trade = {"symbol": "NIFTY", "direction": "CE"}  # no entry/SL/target at all
    rr = {"exit_reason": "NOT_ENTERED"}
    assert outcome_classifier.classify_outcome(trade, rr) == "INVALID_SIGNAL"


def test_outcome_classifier_invalid_signal_bad_sl_side():
    trade = _trade(stop_loss=101)  # SL above/at entry_low -- nonsensical for a long
    rr = {"exit_reason": "SL_HIT"}
    assert outcome_classifier.classify_outcome(trade, rr) == "INVALID_SIGNAL"


def test_outcome_classifier_breakeven_eod():
    trade = _trade()
    rr = {"exit_reason": "EOD_CLOSED", "entry": 100.0, "exit_price": 100.05}
    assert outcome_classifier.classify_outcome(trade, rr) == "BREAKEVEN"


def test_outcome_classifier_ambiguous_is_time_exit():
    trade = _trade()
    rr = {"exit_reason": "AMBIGUOUS"}
    assert outcome_classifier.classify_outcome(trade, rr) == "TIME_EXIT"


# ---------------------------------------------------------------------------
# Performance Analytics (analytics validation)
# ---------------------------------------------------------------------------

def test_performance_analytics_basic_stats():
    records = [
        {"outcome": "WIN", "pnl_pct": 10.0, "max_drawdown_pct": 2.0, "holding_minutes": 30},
        {"outcome": "WIN", "pnl_pct": 5.0, "max_drawdown_pct": 1.0, "holding_minutes": 20},
        {"outcome": "LOSS", "pnl_pct": -8.0, "max_drawdown_pct": 8.0, "holding_minutes": 15},
        {"outcome": "NO_ENTRY", "pnl_pct": 0.0, "max_drawdown_pct": 0.0, "holding_minutes": None},
    ]
    perf = performance_analytics.compute_performance(records)
    assert perf["total_trades"] == 3   # NO_ENTRY excluded
    assert perf["wins"] == 2 and perf["losses"] == 1
    assert perf["win_rate"] == round(2 / 3 * 100, 2)
    assert perf["avg_profit_pct"] == round((10.0 + 5.0) / 2, 2)
    assert perf["avg_loss_pct"] == -8.0
    assert perf["profit_factor"] == round(15.0 / 8.0, 2)
    assert perf["max_drawdown_pct"] == 8.0
    assert perf["max_consecutive_wins"] == 2
    assert perf["max_consecutive_losses"] == 1


def test_performance_analytics_empty_input():
    perf = performance_analytics.compute_performance([])
    assert perf["total_trades"] == 0
    assert perf["win_rate"] == 0.0
    assert perf["profit_factor"] == 0.0


def test_performance_analytics_all_wins_profit_factor_infinite():
    records = [{"outcome": "WIN", "pnl_pct": 5.0, "max_drawdown_pct": 0.0, "holding_minutes": 10}]
    perf = performance_analytics.compute_performance(records)
    assert perf["profit_factor"] == float("inf")


def test_performance_analytics_streaks_reset_on_breakeven():
    records = [
        {"outcome": "WIN", "pnl_pct": 1.0, "max_drawdown_pct": 0.0, "holding_minutes": 1},
        {"outcome": "WIN", "pnl_pct": 1.0, "max_drawdown_pct": 0.0, "holding_minutes": 1},
        {"outcome": "BREAKEVEN", "pnl_pct": 0.0, "max_drawdown_pct": 0.0, "holding_minutes": 1},
        {"outcome": "WIN", "pnl_pct": 1.0, "max_drawdown_pct": 0.0, "holding_minutes": 1},
    ]
    perf = performance_analytics.compute_performance(records)
    assert perf["max_consecutive_wins"] == 2


# ---------------------------------------------------------------------------
# Missed Opportunity / False Signal
# ---------------------------------------------------------------------------

def test_missed_opportunity_good_trade_rejected():
    rejected = [{
        "symbol": "BANKNIFTY", "direction": "CE", "signal_time": "t1", "score": 70,
        "replay_result": {"entry": 100.0, "max_profit_pct": 20.0},
    }]
    findings = missed_opportunity.find_good_trades_rejected(rejected)
    assert len(findings) == 1
    assert findings[0]["type"] == "GOOD_TRADE_REJECTED"


def test_missed_opportunity_bad_trade_accepted():
    alerted = [{
        "symbol": "NIFTY", "direction": "CE", "signal_time": "t1", "score": 80,
        "outcome": "LOSS", "replay_result": {"time_to_stop_loss": 3},
    }]
    findings = missed_opportunity.find_bad_trades_accepted(alerted)
    assert len(findings) == 1
    assert findings[0]["type"] == "BAD_TRADE_ACCEPTED"


def test_false_signal_low_quality_entry():
    from config import ENTRY_SCORE
    trade = {"symbol": "NIFTY", "direction": "CE", "signal_time": "t1",
              "score": ENTRY_SCORE + 1, "outcome": "LOSS"}
    finding = false_signal.detect_low_quality_entry(trade)
    assert finding is not None
    assert finding["type"] == "LOW_QUALITY_ENTRY"


def test_false_signal_poor_option_selection():
    trade = {"symbol": "NIFTY", "direction": "CE", "signal_time": "t1",
              "outcome": "LOSS", "options_intelligence": {"score": 30}}
    finding = false_signal.detect_poor_option_selection(trade)
    assert finding is not None
    assert finding["type"] == "POOR_OPTION_SELECTION"


def test_false_signal_none_when_healthy():
    trade = {"symbol": "NIFTY", "direction": "CE", "signal_time": "t1",
              "outcome": "WIN", "options_intelligence": {"score": 90}}
    assert false_signal.detect_poor_option_selection(trade) is None


# ---------------------------------------------------------------------------
# Learning Engine
# ---------------------------------------------------------------------------

def test_learning_engine_groups_and_ranks():
    records = []
    for i in range(6):
        records.append({"outcome": "WIN", "pnl_pct": 10.0, "max_drawdown_pct": 1.0,
                         "holding_minutes": 10, "market_regime": "TRENDING"})
    for i in range(6):
        records.append({"outcome": "LOSS", "pnl_pct": -5.0, "max_drawdown_pct": 5.0,
                         "holding_minutes": 10, "market_regime": "VOLATILE"})
    ranked = learning_engine.learn_by_market_condition(records)
    assert len(ranked) == 2
    assert ranked[0]["group"] == "TRENDING"  # higher expectancy first
    assert ranked[-1]["group"] == "VOLATILE"


def test_learning_engine_respects_min_sample_size():
    from config import REPLAY_LEARNING_MIN_SAMPLE_SIZE
    records = [{"outcome": "WIN", "pnl_pct": 10.0, "max_drawdown_pct": 1.0,
                "holding_minutes": 10, "market_regime": "TRENDING"}] * (REPLAY_LEARNING_MIN_SAMPLE_SIZE - 1)
    ranked = learning_engine.learn_by_market_condition(records)
    assert ranked == []  # below minimum sample size, must not surface


# ---------------------------------------------------------------------------
# Scoring Calibration
# ---------------------------------------------------------------------------

def test_scoring_calibration_flags_overconfidence():
    # "Very High" band (85-100) predicts ~92.5% but only wins 20% -> should flag
    records = []
    for i in range(4):
        records.append({"outcome": "LOSS", "pnl_pct": -5.0, "confidence_score": 90})
    records.append({"outcome": "WIN", "pnl_pct": 5.0, "confidence_score": 90})
    result = scoring_calibration.calibrate(records)
    very_high = [b for b in result["bands"] if b["band"] == "Very High"][0]
    assert very_high["sample_size"] == 5
    assert very_high["actual_win_rate"] == 20.0
    assert len(result["recommendations"]) == 1
    assert result["recommendations"][0]["band"] == "Very High"


def test_scoring_calibration_no_recommendation_when_calibrated():
    # midpoint ~92.5 for Very High band; give it a matching win rate
    records = [{"outcome": "WIN" if i < 9 else "LOSS", "pnl_pct": 5.0 if i < 9 else -5.0,
                "confidence_score": 90} for i in range(10)]
    result = scoring_calibration.calibrate(records)
    assert result["recommendations"] == []


# ---------------------------------------------------------------------------
# Health Engine
# ---------------------------------------------------------------------------

def test_health_engine_all_wins_scores_high():
    alerted = [{"outcome": "WIN"} for _ in range(10)]
    health = health_engine.compute_health_score(alerted)
    assert health["scanner_health_score"] > 60
    assert health["band"] in ("Excellent", "Good")


def test_health_engine_all_losses_scores_low():
    alerted = [{"outcome": "LOSS"} for _ in range(10)]
    health = health_engine.compute_health_score(alerted)
    assert health["scanner_health_score"] < 50


def test_health_engine_empty_input_no_crash():
    health = health_engine.compute_health_score([])
    assert health["sample_size"] == 0
    assert 0 <= health["scanner_health_score"] <= 100


# ---------------------------------------------------------------------------
# Trade Journal (entry building only -- no git/network I/O in tests)
# ---------------------------------------------------------------------------

def test_trade_journal_build_entry_shape():
    trade = {"trade_id": "NIFTY-CE-001", "symbol": "NIFTY", "direction": "CE",
              "signal_time": "2026-07-13T09:30:00+05:30", "confidence_score": 80,
              "probability_pct": 75, "trade_quality": 82}
    rr = {"entry": 100.0, "stop_loss": 90.0, "target_1": 110.0, "target_2": 120.0,
          "target_3": None, "exit_reason": "T1_HIT", "exit_price": 110.0,
          "max_drawdown_pct": 1.5, "time_to_target": 12}
    entry = trade_journal.build_journal_entry(trade, rr, "WIN")
    assert entry["symbol"] == "NIFTY"
    assert entry["outcome"] == "WIN"
    assert entry["entry"] == 100.0
    assert entry["targets"]["target_1"] == 110.0
    assert entry["pnl_pct"] == 10.0
    assert entry["holding_minutes"] == 12


# ---------------------------------------------------------------------------
# Regression: full pipeline stays stable for a fixed synthetic trade
# ---------------------------------------------------------------------------

def test_regression_full_pipeline_fixed_trade():
    # t2 is set, so hitting T1 alone is a checkpoint, not an exit --
    # the trade only exits once the highest DEFINED target (T2 here),
    # SL, or EOD is reached. Candles here touch T1 (110) but never T2
    # (120), so the fixed-data expectation is an EOD_CLOSED win.
    trade = _trade(t1=110, t2=120, t3=None)
    candles = _one_min_series((9, 30), [101, 104, 108, 112, 115], base=100, wiggle=0.1)
    rr = signal_replay.replay_signal(trade, candles)
    outcome = outcome_classifier.classify_outcome(trade, rr)
    assert rr["exit_reason"] == "EOD_CLOSED"
    assert outcome == "WIN"
    perf = performance_analytics.compute_performance([
        {"outcome": outcome, "pnl_pct": (rr["exit_price"] - rr["entry"]) / rr["entry"] * 100,
         "max_drawdown_pct": rr["max_drawdown_pct"], "holding_minutes": rr["time_to_target"]}
    ])
    assert perf["wins"] == 1
    assert perf["win_rate"] == 100.0


# ---------------------------------------------------------------------------
# Performance test: replay should stay fast even over a full session's candles
# ---------------------------------------------------------------------------

def test_performance_replay_speed_smoke_test():
    trade = _trade(t1=1_000_000, t2=None)  # unreachable target -> forces full-day walk
    candles = _one_min_series((9, 15), [100 + (i % 7) for i in range(375)])  # ~a full session
    start = time.time()
    for _ in range(50):
        signal_replay.replay_signal(trade, candles)
    elapsed = time.time() - start
    assert elapsed < 5.0, f"50 full-session replays took {elapsed:.2f}s -- investigate a perf regression"


def _run_all():
    tests = [obj for name, obj in list(globals().items())
             if name.startswith("test_") and callable(obj)]
    passed = 0
    for t in tests:
        t()
        passed += 1
        print(f"OK   {t.__name__}")
    print(f"\n{passed}/{len(tests)} tests passed.")


if __name__ == "__main__":
    _run_all()
