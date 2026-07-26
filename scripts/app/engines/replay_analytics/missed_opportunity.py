"""
app/engines/replay_analytics/missed_opportunity.py -- Missed
Opportunity Engine.
------------------------------------------------------------------------------
Looks across BOTH alerted trades and rejected candidates (candidates
that scored below config.ENTRY_SCORE and never fired, if the caller
supplies them -- see trade_journal.py's "rejected_candidates" list)
to flag:
    - good trades rejected     (a rejected candidate that would have
                                 hit target if replayed forward)
    - bad trades accepted      (an alerted trade that hit SL fast /
                                 never should have qualified)
    - late entries             (entry filled well after the signal candle)
    - early exits               (price kept moving favorably well past
                                 the actual exit)
    - missed breakouts/reversals (rejected candidate whose subsequent
                                 move was a genuine breakout/reversal)

Every finding includes a short, human-readable improvement suggestion
-- this module never changes config or scoring itself (see
scoring_calibration.py for the "compare and recommend" half of that).
"""

from config import (
    REPLAY_MISSED_OPPORTUNITY_MIN_MOVE_PCT, REPLAY_LATE_ENTRY_MAX_CANDLES,
    REPLAY_EARLY_EXIT_MIN_REMAINING_MOVE_PCT,
)


def find_good_trades_rejected(rejected_candidates: list) -> list:
    """
    rejected_candidates: list of dicts, each a candidate that never
    alerted, carrying its own forward-replay result under
    "replay_result" (produced the same way as an alerted trade --
    signal_replay.replay_signal() run against the candidate's own
    would-be entry/SL/targets, even though no alert was ever sent).
    """
    findings = []
    for cand in rejected_candidates:
        rr = cand.get("replay_result") or {}
        entry = rr.get("entry")
        max_profit_pct = rr.get("max_profit_pct", 0.0)
        if entry is not None and max_profit_pct >= REPLAY_MISSED_OPPORTUNITY_MIN_MOVE_PCT * 100:
            findings.append({
                "type": "GOOD_TRADE_REJECTED",
                "symbol": cand.get("symbol"), "direction": cand.get("direction"),
                "signal_time": cand.get("signal_time"),
                "score": cand.get("score"), "max_profit_pct": max_profit_pct,
                "suggestion": (
                    f"{cand.get('symbol')} {cand.get('direction')} scored "
                    f"{cand.get('score')} and was rejected, but would have "
                    f"moved {max_profit_pct:.1f}% -- review whether the "
                    f"scoring floor is too strict for this setup type."
                ),
            })
    return findings


def find_bad_trades_accepted(alerted_trades: list) -> list:
    """alerted_trades: enriched records with 'outcome' + 'replay_result'."""
    findings = []
    for t in alerted_trades:
        rr = t.get("replay_result") or {}
        if t.get("outcome") == "LOSS" and rr.get("time_to_stop_loss") is not None \
                and rr.get("time_to_stop_loss") <= 5:
            findings.append({
                "type": "BAD_TRADE_ACCEPTED",
                "symbol": t.get("symbol"), "direction": t.get("direction"),
                "signal_time": t.get("signal_time"), "score": t.get("score"),
                "time_to_stop_loss": rr.get("time_to_stop_loss"),
                "suggestion": (
                    f"{t.get('symbol')} {t.get('direction')} hit stop loss "
                    f"within {rr.get('time_to_stop_loss'):.0f} min of entry "
                    f"-- check whether the entry validation gate needs a "
                    f"stricter confirmation for this pattern."
                ),
            })
    return findings


def find_late_entries(alerted_trades: list) -> list:
    findings = []
    for t in alerted_trades:
        rr = t.get("replay_result") or {}
        idx = rr.get("entry_candle_index")
        if idx is not None and idx > REPLAY_LATE_ENTRY_MAX_CANDLES:
            findings.append({
                "type": "LATE_ENTRY", "symbol": t.get("symbol"),
                "direction": t.get("direction"), "signal_time": t.get("signal_time"),
                "candles_to_fill": idx,
                "suggestion": (
                    f"{t.get('symbol')} {t.get('direction')} took {idx} candles "
                    f"to fill -- entry zone may be too tight or too far from "
                    f"the signal price for this timeframe."
                ),
            })
    return findings


def find_early_exits(alerted_trades: list) -> list:
    findings = []
    for t in alerted_trades:
        rr = t.get("replay_result") or {}
        if rr.get("exit_reason") in ("T1_HIT",) and rr.get("max_profit_pct", 0.0) > 0:
            realized = rr.get("target_1")
            entry = rr.get("entry")
            if entry and realized:
                realized_pct = (realized - entry) / entry * 100
                remaining = rr["max_profit_pct"] - realized_pct
                if remaining >= REPLAY_EARLY_EXIT_MIN_REMAINING_MOVE_PCT * 100:
                    findings.append({
                        "type": "EARLY_EXIT", "symbol": t.get("symbol"),
                        "direction": t.get("direction"), "signal_time": t.get("signal_time"),
                        "remaining_move_pct": round(remaining, 2),
                        "suggestion": (
                            f"{t.get('symbol')} {t.get('direction')} exited at "
                            f"Target 1 but price moved {remaining:.1f}% further "
                            f"-- consider trailing to Target 2/3 for this setup type."
                        ),
                    })
    return findings


def analyze_missed_opportunities(alerted_trades: list, rejected_candidates: list = None) -> dict:
    rejected_candidates = rejected_candidates or []
    return {
        "good_trades_rejected": find_good_trades_rejected(rejected_candidates),
        "bad_trades_accepted": find_bad_trades_accepted(alerted_trades),
        "late_entries": find_late_entries(alerted_trades),
        "early_exits": find_early_exits(alerted_trades),
    }
