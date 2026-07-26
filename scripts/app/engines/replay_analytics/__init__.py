"""
app/engines/replay_analytics/ -- Part 7: Replay, Learning & Analytics Engine.
------------------------------------------------------------------------------
Offline-only engine that replays historical candles against past
signals, measures how the scanner actually performed, and produces
statistical (non-ML) learning recommendations. NEVER places orders and
NEVER runs inside the live scanner/tracker loop -- see engine.py's
module docstring for exactly how (and when) this is invoked, and
config.py's "Part 7" section for every tunable.

Modules:
  replay_engine.py         Candle-by-candle multi-timeframe replay
                            driver (resamples 1-min candles up; never
                            re-fetches per timeframe).
  signal_replay.py          Per-signal replay: entry/SL/T1/T2/T3, time
                            to target/SL, max profit/drawdown, exit
                            reason. Reuses tracker.py's lifecycle
                            rules, extended with a Target 3 leg.
  outcome_classifier.py      WIN / LOSS / BREAKEVEN / NO_ENTRY /
                            TIME_EXIT / INVALID_SIGNAL.
  performance_analytics.py  Win rate, profit factor, expectancy,
                            drawdown, streaks, recovery factor.
  missed_opportunity.py     Good-trades-rejected / bad-trades-accepted
                            / late-entry / early-exit / missed
                            breakout-reversal detection + suggestions.
  false_signal.py           False breakout/breakdown, weak momentum,
                            low-quality-entry, poor-option-selection
                            detection + reasons.
  learning_engine.py        Statistical (NOT machine-learning) grouping
                            of results by market condition, timeframe,
                            entry type, pattern, option characteristic.
  scoring_calibration.py    Predicted-confidence vs actual-result
                            comparison; RECOMMENDATIONS only, never
                            auto-changes a production threshold.
  health_engine.py          Scanner Health Score (0-100).
  trade_journal.py          Structured per-trade journal, persisted via
                            daily_cache.py (same state-branch mechanism
                            already used by opportunity_score.py).
  periodic_reports.py       Daily / Weekly / Monthly analytics rollups.
  reporting.py              Optional CSV / JSON / summary export.
  engine.py                 run_daily_replay_analytics() -- the single
                            entry point a scheduled job calls.

Each module is independently importable/testable, following the same
convention app/engines/early_breakout/ and app/engines/decision_engine/
established -- nothing here imports another engine folder's internals,
only shared scripts/ modules (config, logging_setup, state, tracker,
daily_cache).
"""
