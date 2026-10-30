"""
app/engines/replay_analytics/tests/test_replay_analytics.py
------------------------------------------------------------------------------
Plain assert-based tests, no pytest dependency -- same convention as
app/engines/decision_engine/tests/test_decision_engine.py. All
synthetic, deterministic input, no broker/network access.

Covers: Performance Analytics arithmetic, Trade Journal entry-building,
Scanner Health Engine scoring.

(2026-10-30, at the user's explicit request: this file used to also
cover Replay Engine resampling, Signal Replay, Outcome Classifier,
Missed Opportunity / False Signal, Learning Engine, and Scoring
Calibration -- all part of the "Part 7: Replay, Learning & Analytics
Engine" offline pipeline (app/engines/replay_analytics/engine.py's own
docstring: "a scheduled job calls" run_daily_replay_analytics()).
Confirmed this session that no GitHub Actions workflow ever actually
called that job -- that part of the pipeline was built but never
wired in, and was removed, along with the modules it tested
(replay_engine.py, signal_replay.py, outcome_classifier.py,
missed_opportunity.py, false_signal.py, learning_engine.py,
scoring_calibration.py) and the pipeline's own entry point (engine.py).

health_engine.py was NOT removed -- it is genuinely still used, by
app/backend/services/analytics_service.py's get_scanner_health(),
itself wired to the dashboard's GET /analytics health route
(app/backend/api/routes/analytics.py). performance_analytics.py,
trade_journal.py, and health_engine.py are the three modules from this
package genuinely still used; their tests are kept below.)

Run with:
    python scripts/app/engines/replay_analytics/tests/test_replay_analytics.py
Exits non-zero (via AssertionError) on the first failing check.
"""

import sys
import os

_here = os.path.dirname(os.path.abspath(__file__))
_scripts_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_here))))
sys.path.insert(0, _scripts_dir)

from app.engines.replay_analytics import performance_analytics, trade_journal, health_engine


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
# Scanner Health Engine
# ---------------------------------------------------------------------------

def test_health_engine_basic_score_and_band():
    trades = [
        {"outcome": "WIN", "decision": {"decision": "Trade Approved"}},
        {"outcome": "WIN", "decision": {"decision": "Trade Approved"}},
        {"outcome": "LOSS", "decision": {"decision": "Trade Rejected"}},
        {"outcome": "NO_ENTRY"},  # excluded from "tradeable"
    ]
    result = health_engine.compute_health_score(trades)
    assert result["sample_size"] == 3
    assert result["signal_accuracy"] == round(2 / 3 * 100, 1)
    # both decided trades: WIN+Approved agrees, LOSS+Rejected agrees -> 100% decision accuracy
    assert result["decision_accuracy"] == 100.0
    assert result["false_positive_rate"] == round(1 / 3 * 100, 1)
    assert result["false_negative_rate"] == 0.0  # no rejected_candidates passed in
    assert 0.0 <= result["scanner_health_score"] <= 100.0
    assert result["band"] in ("Excellent", "Good", "Fair", "Poor")


def test_health_engine_empty_input():
    # With zero tradeable trades, both accuracy components are 0.0 and
    # both penalty components get full marks (no positives/negatives to
    # penalize), so the score is NOT 0 -- it's just the false-positive +
    # false-negative penalty weights added together (see
    # REPLAY_HEALTH_SCORE_WEIGHTS in config.py: 20 + 15 = 35 by default).
    result = health_engine.compute_health_score([])
    assert result["sample_size"] == 0
    assert result["signal_accuracy"] == 0.0
    assert result["decision_accuracy"] == 0.0
    assert result["false_positive_rate"] == 0.0
    assert result["false_negative_rate"] == 0.0
    assert result["band"] == "Poor"


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
