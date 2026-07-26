"""
app/engines/replay_analytics/health_engine.py -- Scanner Health Engine.
------------------------------------------------------------------------------
Composite 0-100 Scanner Health Score from four components:
    Signal Accuracy      -- % of alerted trades that were WIN
    Decision Accuracy     -- % of alerted trades the Decision Engine
                             (Part 6) would have approved AND that won,
                             plus trades it rejected that would have lost
                             (i.e. the Decision Engine agreeing with reality)
    False Positive Rate   -- alerted trades that lost (inverted into a
                             penalty: higher FP rate -> lower score)
    False Negative Rate    -- good rejected candidates that were missed
                             (inverted into a penalty)
Weights: config.REPLAY_HEALTH_SCORE_WEIGHTS (sum to 100).
"""

from config import REPLAY_HEALTH_SCORE_WEIGHTS, REPLAY_HEALTH_SCORE_BANDS


def _band_label(score: float) -> str:
    for floor, label in REPLAY_HEALTH_SCORE_BANDS:
        if score >= floor:
            return label
    return REPLAY_HEALTH_SCORE_BANDS[-1][1]


def compute_health_score(alerted_trades: list, rejected_candidates: list = None,
                          missed_findings: dict = None) -> dict:
    rejected_candidates = rejected_candidates or []
    missed_findings = missed_findings or {}

    tradeable = [t for t in alerted_trades if t.get("outcome") not in ("NO_ENTRY", "INVALID_SIGNAL")]
    total = len(tradeable)

    signal_accuracy = (
        round(len([t for t in tradeable if t["outcome"] == "WIN"]) / total * 100, 1) if total else 0.0
    )

    # Decision accuracy: of trades where trade["decision"]["decision"]
    # is present (Part 6 output already attached), how often did
    # "Trade Approved" line up with a WIN and "Trade Rejected" line up
    # with a LOSS (i.e. the decision engine would have been right either way)?
    decided = [t for t in tradeable if isinstance(t.get("decision"), dict)]
    correct_decisions = 0
    for t in decided:
        approved = t["decision"].get("decision") == "Trade Approved"
        won = t["outcome"] == "WIN"
        if approved == won:
            correct_decisions += 1
    decision_accuracy = round(correct_decisions / len(decided) * 100, 1) if decided else signal_accuracy

    false_positive_rate = (
        round(len([t for t in tradeable if t["outcome"] == "LOSS"]) / total * 100, 1) if total else 0.0
    )

    good_rejected = len(missed_findings.get("good_trades_rejected", []))
    false_negative_rate = (
        round(good_rejected / (len(rejected_candidates) or 1) * 100, 1) if rejected_candidates else 0.0
    )

    weights = REPLAY_HEALTH_SCORE_WEIGHTS
    score = (
        signal_accuracy * weights["signal_accuracy"] / 100
        + decision_accuracy * weights["decision_accuracy"] / 100
        + (100 - false_positive_rate) * weights["false_positive_penalty"] / 100
        + (100 - false_negative_rate) * weights["false_negative_penalty"] / 100
    )
    score = round(min(100.0, max(0.0, score)), 1)

    return {
        "scanner_health_score": score,
        "band": _band_label(score),
        "signal_accuracy": signal_accuracy,
        "decision_accuracy": decision_accuracy,
        "false_positive_rate": false_positive_rate,
        "false_negative_rate": false_negative_rate,
        "sample_size": total,
    }
