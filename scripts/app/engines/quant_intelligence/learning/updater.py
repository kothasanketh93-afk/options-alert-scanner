"""
learning/updater.py -- Rule-based weight update logic.

Given the factor scores that were used at decision time and the
realized outcome (WIN/LOSS/BREAKEVEN), compute a per-factor delta to
feed into probability.weights.AdaptiveWeightManager.nudge().

Rule (deliberately simple and auditable, per "rule-based learning, no
ML framework required"):
  - On a WIN: factors that scored high (>=0.6) get a positive delta
    proportional to how high they scored (they "called it right").
    Factors that scored low (<=0.3) get a small negative delta (they
    "missed" a winning setup).
  - On a LOSS: the mirror image -- high-scoring factors get a negative
    delta (they were confidently wrong), low-scoring factors get a
    small positive delta (they correctly flagged caution).
  - BREAKEVEN: no update.
"""

from __future__ import annotations

from typing import Dict


def compute_factor_deltas(factor_scores: Dict[str, float], outcome: str) -> Dict[str, float]:
    outcome = outcome.upper()
    if outcome not in ("WIN", "LOSS"):
        return {}

    deltas: Dict[str, float] = {}
    sign = 1.0 if outcome == "WIN" else -1.0

    for factor, value in factor_scores.items():
        if value >= 0.6:
            deltas[factor] = sign * value           # confident + right/wrong
        elif value <= 0.3:
            deltas[factor] = -sign * (1 - value) * 0.3  # cautious, small counter-nudge
        else:
            deltas[factor] = 0.0  # ambiguous mid-range reads aren't penalized/rewarded
    return deltas
