"""
pattern/statistics.py -- Aggregate historical outcomes into pattern stats.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def confidence_from_sample(sample_size: int, min_sample_for_confidence: int) -> str:
    if sample_size == 0:
        return "NONE"
    if sample_size < max(3, min_sample_for_confidence // 3):
        return "VERY LOW"
    if sample_size < max(5, min_sample_for_confidence // 2):
        return "LOW"
    if sample_size < min_sample_for_confidence:
        return "MEDIUM"
    if sample_size < min_sample_for_confidence * 3:
        return "HIGH"
    return "VERY HIGH"


def summarize(outcomes: List[Dict[str, Any]], min_sample_for_confidence: int) -> Dict[str, Any]:
    sample_size = len(outcomes)
    if sample_size == 0:
        return {
            "win_pct": None,
            "loss_pct": None,
            "avg_target_pct": None,
            "avg_sl_pct": None,
            "sample_size": 0,
            "confidence": confidence_from_sample(0, min_sample_for_confidence),
        }

    wins = [o for o in outcomes if o.get("outcome") == "WIN"]
    losses = [o for o in outcomes if o.get("outcome") == "LOSS"]

    win_pct = round(100 * len(wins) / sample_size, 2)
    loss_pct = round(100 * len(losses) / sample_size, 2)

    target_values = [o["target_pct"] for o in wins if o.get("target_pct") is not None]
    sl_values = [o["sl_pct"] for o in losses if o.get("sl_pct") is not None]

    avg_target_pct = round(sum(target_values) / len(target_values), 3) if target_values else None
    avg_sl_pct = round(sum(sl_values) / len(sl_values), 3) if sl_values else None

    return {
        "win_pct": win_pct,
        "loss_pct": loss_pct,
        "avg_target_pct": avg_target_pct,
        "avg_sl_pct": avg_sl_pct,
        "sample_size": sample_size,
        "confidence": confidence_from_sample(sample_size, min_sample_for_confidence),
    }
