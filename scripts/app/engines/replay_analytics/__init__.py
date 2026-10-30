"""
app/engines/replay_analytics/ -- Performance Analytics + Trade Journal +
Scanner Health Engine (dashboard-facing, on-demand).
------------------------------------------------------------------------------
Modules:
  performance_analytics.py  Win rate, profit factor, expectancy,
                            drawdown, streaks, recovery factor. Used by
                            app/backend/services/analytics_service.py
                            and lifecycle_analytics.py's own historical
                            BTST-success-rate computation before that
                            was removed (2026-10-30).
  trade_journal.py          Structured per-trade journal, persisted via
                            daily_cache.py (same state-branch mechanism
                            already used by opportunity_score.py). Used
                            by app/backend/services/replay_service.py.
  health_engine.py          Composite 0-100 Scanner Health Score (signal
                            accuracy, decision accuracy, false-positive/
                            negative rate). Used by
                            app/backend/services/analytics_service.py's
                            get_scanner_health(), which is wired to the
                            dashboard's GET /analytics health route
                            (app/backend/api/routes/analytics.py) --
                            confirmed a real importer, see note below.

(2026-10-30, at the user's explicit request, as part of making this
app intraday-only: this package used to be a much larger "Part 7:
Replay, Learning & Analytics Engine" -- an offline pipeline that
replayed historical candles against past signals and produced
statistical learning recommendations, with its own entry point
(engine.py's run_daily_replay_analytics(), documented as something "a
scheduled job calls"). Confirmed this session that no GitHub Actions
workflow ever actually called that job -- replay_engine.py,
signal_replay.py, outcome_classifier.py, missed_opportunity.py,
false_signal.py, learning_engine.py, scoring_calibration.py,
periodic_reports.py, and reporting.py were built but never wired in
anywhere, and were removed, along with engine.py itself (the offline
pipeline's own entry point).

health_engine.py was INITIALLY included in that same removal list by
mistake -- it was corrected here after actually grepping its callers
and finding analytics_service.py's get_scanner_health() calls
health_engine.compute_health_score() directly, and that function is
itself wired to a live dashboard route. health_engine.py was restored
before anything was deleted from the real repo. performance_analytics.py,
trade_journal.py, and health_engine.py are the three modules from this
package genuinely still used, and are unaffected by the removal above.)
"""
