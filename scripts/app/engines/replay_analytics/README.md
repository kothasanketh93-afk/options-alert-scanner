# Replay, Learning & Analytics Engine (Part 7)

Offline-only engine that replays historical candles against past
signals, measures how the scanner actually performed, and produces
statistical (non-ML) learning recommendations.

**Never places orders. Never runs inside the live scanner/tracker
loop. Never auto-changes a production threshold.**

## Purpose

Give the project a feedback loop: for every alerted trade (and,
optionally, every candidate the scanner rejected), replay the real
historical candles and find out what actually would have happened,
then roll that up into win rate / profit factor / expectancy,
learning-by-condition, scoring-calibration, and a single Scanner
Health Score (0-100).

## Responsibilities

- Replay 1-minute candles candle-by-candle, resampled up to every
  timeframe in `config.REPLAY_TIMEFRAMES` (`replay_engine.py`).
- Determine Entry/SL/Target 1-3, timing, max profit/drawdown, and exit
  reason for one signal (`signal_replay.py`), extending tracker.py's
  live lifecycle rules with a Target 3 leg and richer timing fields.
- Classify every trade into WIN / LOSS / BREAKEVEN / NO_ENTRY /
  TIME_EXIT / INVALID_SIGNAL (`outcome_classifier.py`).
- Compute win rate, profit factor, expectancy, drawdown, streaks,
  recovery factor (`performance_analytics.py`).
- Flag good trades rejected, bad trades accepted, late entries, early
  exits (`missed_opportunity.py`).
- Flag false breakouts/breakdowns, weak momentum, low-quality entries,
  poor option selection (`false_signal.py`).
- Statistically (not via ML) group results by market condition,
  timeframe, entry type, pattern, option characteristic
  (`learning_engine.py`).
- Compare predicted confidence to actual result and generate
  **recommendations only** (`scoring_calibration.py`).
- Produce the 0-100 Scanner Health Score (`health_engine.py`).
- Maintain the structured Trade Journal, persisted via `daily_cache.py`
  (`trade_journal.py`).
- Generate Daily / Weekly / Monthly analytics rollups
  (`periodic_reports.py`).
- Optional CSV/JSON export, all independently toggleable
  (`reporting.py`).
- **Not** responsible for: placing orders, changing
  `DECISION_ENGINE_MIN_CONFIDENCE`/`ENTRY_SCORE`/any live threshold,
  or running inside `main.py`'s scan loop.

## Entry point

```python
from app.engines.replay_analytics import engine as replay_analytics

result = replay_analytics.run_replay_analytics(
    date_str="2026-07-25",
    candles_by_trade={trade_id: one_min_candles, ...},
    alerted_trades=[...],       # closed trades for that date
    rejected_candidates=[...],  # optional
)
replay_analytics.run_weekly_monthly_rollups("2026-07-25")
```

## Configuration

Every tunable lives in `config.py`'s "Part 7" section --
`REPLAY_ANALYTICS_ENABLED` is the master flag; every sub-report
(daily/weekly/monthly analytics, CSV, JSON) has its own flag too.

## Tests

```
python scripts/app/engines/replay_analytics/tests/test_replay_analytics.py
```

Plain assert-based, synthetic candle data, no broker/network access --
same convention as `app/engines/decision_engine/tests/`.
