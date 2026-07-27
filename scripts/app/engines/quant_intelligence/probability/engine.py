"""
probability/engine.py -- Adaptive Probability Engine.

Combines per-factor 0-1 strength scores (scorer.py) with adaptive,
persisted weights (weights.py) into a single 0-100 probability plus a
discrete confidence band, with human-readable reasons attached.
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..models.signal import Signal
from ..models.result import ProbabilityResult
from .models import FactorScore, ScoringBreakdown
from .scorer import FACTOR_SCORERS
from .weights import AdaptiveWeightManager


class ProbabilityEngine:
    def __init__(self, config: Dict[str, Any], weight_manager: AdaptiveWeightManager) -> None:
        prob_cfg = config.get("probability", {})
        self._bands = prob_cfg.get(
            "confidence_bands",
            {"VERY_LOW": 0, "LOW": 35, "MEDIUM": 55, "HIGH": 72, "VERY_HIGH": 85},
        )
        self._weight_manager = weight_manager

    def _confidence_for(self, probability: float) -> str:
        # Sort bands by threshold ascending, pick the highest one <= probability.
        ordered = sorted(self._bands.items(), key=lambda kv: kv[1])
        label = ordered[0][0]
        for name, threshold in ordered:
            if probability >= threshold:
                label = name
        return label.replace("_", " ")

    def score(self, signal: Signal) -> ProbabilityResult:
        weights = self._weight_manager.get_weights()
        breakdown = ScoringBreakdown()
        reasons: List[str] = []

        for factor_name, scorer_fn in FACTOR_SCORERS.items():
            weight = weights.get(factor_name, 0.0)
            try:
                value = scorer_fn(signal)
            except Exception:  # defensive: a single bad factor must not sink scoring
                value = 0.0
            contribution = value * weight * 100
            breakdown.factors.append(
                FactorScore(name=factor_name, value=value, weight=weight, contribution=contribution)
            )

        raw_probability = sum(f.contribution for f in breakdown.factors)
        # Weights already sum to 1.0, so raw_probability is naturally 0-100.
        probability = round(max(0.0, min(100.0, raw_probability)), 2)
        breakdown.raw_probability = probability

        # Build reasons from the strongest positive and weakest contributors.
        ranked = sorted(breakdown.factors, key=lambda f: f.contribution, reverse=True)
        for f in ranked[:4]:
            if f.value >= 0.55:
                reasons.append(f"{_label(f.name)} strongly supports {signal.direction or 'the'} setup "
                                f"(strength {f.value:.2f}, weight {f.weight:.2f}).")
        weak = [f for f in breakdown.factors if f.value <= 0.15 and f.weight >= 0.05]
        for f in sorted(weak, key=lambda f: f.weight, reverse=True)[:3]:
            reasons.append(f"{_label(f.name)} provides little/no confirmation (strength {f.value:.2f}).")

        if not reasons:
            reasons.append("No single factor dominates; probability reflects a broad, balanced read.")

        confidence = self._confidence_for(probability)

        return ProbabilityResult(
            probability=probability,
            confidence=confidence,
            factor_scores=breakdown.as_score_dict(),
            factor_weights=breakdown.as_weight_dict(),
            reasons=reasons,
        )


def _label(factor_name: str) -> str:
    return factor_name.replace("_", " ").upper()
