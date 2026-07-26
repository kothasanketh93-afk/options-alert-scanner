"""
app/engines/replay_analytics/scoring_calibration.py -- Scoring
Calibration Engine.
------------------------------------------------------------------------------
Buckets closed trades by their PREDICTED confidence (trade["confidence_score"]
or trade["probability_pct"] -- whichever the trade carries) into
config.REPLAY_CALIBRATION_BANDS, compares each band's ACTUAL win rate
against the band's own midpoint, and flags bands where the gap exceeds
config.REPLAY_CALIBRATION_GAP_THRESHOLD_PCT.

Produces RECOMMENDATIONS ONLY -- per the Part 7 spec this must never
automatically change DECISION_ENGINE_MIN_CONFIDENCE / ENTRY_SCORE or
any other production threshold. A human reads the recommendation and
decides whether to act on it.
"""

from config import REPLAY_CALIBRATION_BANDS, REPLAY_CALIBRATION_GAP_THRESHOLD_PCT
from . import performance_analytics


def _predicted_confidence(trade: dict):
    for field in ("confidence_score", "probability_pct", "trade_score"):
        val = trade.get(field)
        if val is not None:
            return float(val)
    return None


def _band_for(value: float):
    for lo, hi, label in REPLAY_CALIBRATION_BANDS:
        if lo <= value < hi or (hi == 100 and value == 100):
            return (lo, hi, label)
    return None


def calibrate(records: list) -> dict:
    """
    records: closed trades with 'outcome' already set (from
    outcome_classifier) plus whichever confidence/probability field
    they were alerted with.

    Returns {"bands": [...], "recommendations": [...]}.
    """
    buckets = {label: [] for (_, _, label) in REPLAY_CALIBRATION_BANDS}
    for r in records:
        if r.get("outcome") in ("NO_ENTRY", "INVALID_SIGNAL"):
            continue
        pred = _predicted_confidence(r)
        if pred is None:
            continue
        band = _band_for(pred)
        if band:
            buckets[band[2]].append(r)

    bands_out = []
    recommendations = []
    for lo, hi, label in REPLAY_CALIBRATION_BANDS:
        recs = buckets[label]
        midpoint = (lo + hi) / 2 if hi < 100 else (lo + 100) / 2
        if not recs:
            bands_out.append({"band": label, "range": [lo, hi], "sample_size": 0,
                               "actual_win_rate": None, "predicted_midpoint": midpoint, "gap": None})
            continue
        perf = performance_analytics.compute_performance(recs)
        gap = round(perf["win_rate"] - midpoint, 1)
        bands_out.append({
            "band": label, "range": [lo, hi], "sample_size": len(recs),
            "actual_win_rate": perf["win_rate"], "predicted_midpoint": midpoint, "gap": gap,
        })
        if abs(gap) >= REPLAY_CALIBRATION_GAP_THRESHOLD_PCT:
            direction = "overconfident" if gap < 0 else "underconfident"
            recommendations.append({
                "band": label, "gap": gap,
                "recommendation": (
                    f"'{label}' confidence band ({lo}-{hi}) predicted ~{midpoint:.0f}% "
                    f"win rate but actually achieved {perf['win_rate']:.1f}% over "
                    f"{len(recs)} trades -- scoring looks {direction} for this band. "
                    f"Consider reviewing (not auto-changing) the weights that feed "
                    f"this confidence range."
                ),
            })

    return {"bands": bands_out, "recommendations": recommendations}
