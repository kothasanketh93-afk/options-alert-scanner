"""
app/engines/news_intelligence/models/impact.py -- Impact Engine output shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ImpactLevel(str, Enum):
    VERY_HIGH = "very_high"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    IGNORE = "ignore"


@dataclass
class ImpactResult:
    level: ImpactLevel
    score: float                  # 0..100
    expected_duration: str        # e.g. "intraday", "1-3 days", "1-2 weeks"
    urgency: str                  # "immediate" | "same_day" | "monitor"
    confidence: float = 0.0       # 0..1 -- how sure the Impact Engine is of `score`/`level`
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        d["level"] = self.level.value if isinstance(self.level, ImpactLevel) else self.level
        return d
