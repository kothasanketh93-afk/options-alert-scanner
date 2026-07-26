"""
app/engines/replay_analytics/engine.py -- Part 7 entry point.
------------------------------------------------------------------------------
The ONE function a scheduled job (e.g. a nightly GitHub Actions
workflow, separate from trader.yml/premarket.yml/heartbeat.yml) needs
to call. This module NEVER runs inside main.py's live scan/tracker
loop and NEVER places orders -- it only reads historical candles +
state.py's daily state files, replays them, and writes its own
journal/reports.

    from app.engines.replay_analytics import engine as replay_analytics

    result = replay_analytics.run_replay_analytics(
        date_str="2026-07-25",
        candles_by_trade={trade_id: one_min_candles, ...},
        alerted_trades=[...],           # from state.py history for that date
        rejected_candidates=[...],      # optional -- candidates that scored
                                        # below ENTRY_SCORE that day, if the
                                        # caller logged them
    )

Nothing in main.py/scanner.py/tracker.py/state.py needs to change for
this to exist -- see config.py's "Part 7" section for the master flag
(REPLAY_ANALYTICS_ENABLED) and every sub-flag.
"""

from datetime import datetime, timezone, timedelta

from config import REPLAY_ANALYTICS_ENABLED, REPLAY_TIMEFRAMES
from logging_setup import get_logger

from . import (
    replay_engine, signal_replay, outcome_classifier, performance_analytics,
    missed_opportunity, false_signal, learning_engine, scoring_calibration,
    health_engine, trade_journal, periodic_reports, reporting,
)

IST = timezone(timedelta(hours=5, minutes=30))
_logger = get_logger("replay_analytics")


def _enrich_trade(trade: dict, candles: list, replayer: replay_engine.ReplayEngine) -> dict:
    """Runs one alerted trade through Signal Replay + Outcome
    Classification on its native (1-min) timeframe, attaches the
    result, and returns the enriched dict signal_replay/outcome_classifier/
    trade_journal all expect. Multi-timeframe replay (all of
    config.REPLAY_TIMEFRAMES) is available via replayer.resample_all_timeframes()
    for callers that want per-timeframe comparisons -- the native
    1-min replay is what the journal/analytics below are built from,
    since that matches what the live tracker actually saw."""
    rr = signal_replay.replay_signal(trade, candles)
    outcome = outcome_classifier.classify_outcome(trade, rr)
    enriched = dict(trade)
    enriched["replay_result"] = rr
    enriched["outcome"] = outcome
    return enriched


def run_replay_analytics(date_str: str, candles_by_trade: dict,
                          alerted_trades: list, rejected_candidates: list = None) -> dict | None:
    """
    Replays every alerted trade (and, if supplied, every rejected
    candidate) for `date_str`, builds/persists the trade journal,
    computes performance/missed-opportunity/false-signal/learning/
    calibration/health analytics, generates the daily summary, and
    returns everything as one dict. Returns None if
    config.REPLAY_ANALYTICS_ENABLED is False (master off-switch).
    """
    if not REPLAY_ANALYTICS_ENABLED:
        return None

    rejected_candidates = rejected_candidates or []
    replayer = replay_engine.ReplayEngine()

    enriched_alerted = []
    for trade in alerted_trades:
        candles = candles_by_trade.get(trade.get("trade_id"), [])
        enriched_alerted.append(_enrich_trade(trade, candles, replayer))

    enriched_rejected = []
    for cand in rejected_candidates:
        candles = candles_by_trade.get(cand.get("trade_id") or cand.get("candidate_id"), [])
        rr = signal_replay.replay_signal(cand, candles)
        enriched = dict(cand)
        enriched["replay_result"] = rr
        enriched_rejected.append(enriched)

    # Journal
    journal_entries = [
        trade_journal.build_journal_entry(t, t["replay_result"], t["outcome"])
        for t in enriched_alerted
    ]
    trade_journal.append_entries(date_str, journal_entries)

    # Analytics
    performance = performance_analytics.compute_performance(journal_entries)
    candles_by_id = {t.get("trade_id"): candles_by_trade.get(t.get("trade_id"), []) for t in enriched_alerted}
    missed = missed_opportunity.analyze_missed_opportunities(enriched_alerted, enriched_rejected)
    false_signals = false_signal.detect_false_signals(
        [dict(t, outcome=t["outcome"]) for t in enriched_alerted], candles_by_id
    )
    learning = learning_engine.generate_learning_report(journal_entries)
    calibration = scoring_calibration.calibrate(journal_entries)
    health = health_engine.compute_health_score(enriched_alerted, enriched_rejected, missed)
    daily_summary = periodic_reports.generate_daily_summary(date_str)

    result = {
        "date": date_str,
        "performance": performance,
        "missed_opportunities": missed,
        "false_signals": false_signals,
        "learning": learning,
        "calibration": calibration,
        "health": health,
        "daily_summary": daily_summary,
    }

    reporting.write_json_report(result, f"replay_analytics_{date_str}.json")
    reporting.write_csv_report(journal_entries, f"trade_journal_{date_str}.csv")

    _logger.info("replay_analytics: %s -- %s", date_str,
                 reporting.summary_text(performance, health))
    return result


def run_weekly_monthly_rollups(any_date_str: str) -> dict:
    """Convenience wrapper -- generates (and persists) the weekly report
    covering any_date_str's week and the monthly report covering its
    month, using whatever's already in the journal (does NOT re-replay
    anything -- run_replay_analytics() must have already populated the
    journal for the days involved)."""
    return {
        "weekly": periodic_reports.generate_weekly_report(any_date_str),
        "monthly": periodic_reports.generate_monthly_report(any_date_str),
    }
