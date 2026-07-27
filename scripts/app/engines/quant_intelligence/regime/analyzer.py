"""
regime/analyzer.py -- Confidence/scoring helpers used by detector.py.

Kept separate from detector.py so the classification RULES (detector)
are easy to read independent of the scoring MATH (analyzer).
"""

from __future__ import annotations

from typing import List, Tuple


def confidence_label(score: float) -> str:
    if score >= 80:
        return "VERY HIGH"
    if score >= 65:
        return "HIGH"
    if score >= 45:
        return "MEDIUM"
    if score >= 25:
        return "LOW"
    return "VERY LOW"


def blend_scores(scores: List[float]) -> float:
    if not scores:
        return 0.0
    return round(sum(scores) / len(scores), 2)
