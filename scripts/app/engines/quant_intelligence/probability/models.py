"""
probability/models.py -- Internal working types for the probability engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class FactorScore:
    name: str
    value: float          # normalized 0-1 strength
    weight: float          # weight applied
    contribution: float     # value * weight * 100
    note: str = ""


@dataclass
class ScoringBreakdown:
    factors: List[FactorScore] = field(default_factory=list)
    raw_probability: float = 0.0

    def as_score_dict(self) -> Dict[str, float]:
        return {f.name: round(f.value, 4) for f in self.factors}

    def as_weight_dict(self) -> Dict[str, float]:
        return {f.name: round(f.weight, 4) for f in self.factors}
