"""
app/engines/news_intelligence/models/sentiment.py -- Sentiment Engine output shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class SentimentLabel(str, Enum):
    VERY_POSITIVE = "very_positive"
    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"
    VERY_NEGATIVE = "very_negative"


@dataclass
class SentimentResult:
    label: SentimentLabel
    score: float              # -1.0 (very negative) .. +1.0 (very positive)
    confidence: float         # 0.0 .. 1.0
    reasons: list[str] = field(default_factory=list)
    positive_hits: list[str] = field(default_factory=list)
    negative_hits: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        d["label"] = self.label.value if isinstance(self.label, SentimentLabel) else self.label
        return d
