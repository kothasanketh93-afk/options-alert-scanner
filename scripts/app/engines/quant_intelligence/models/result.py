"""
models/result.py -- Output contracts returned by QuantEngine.process().

Every sub-result carries its own `reasons: List[str]` so the
explainability engine can assemble a full narrative, and so callers who
only care about one dimension (e.g. just risk numbers) don't have to
parse the human-readable explanation text.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


@dataclass
class ProbabilityResult:
    probability: float                  # 0-100
    confidence: str                      # VERY LOW | LOW | MEDIUM | HIGH | VERY HIGH
    factor_scores: Dict[str, float] = field(default_factory=dict)   # 0-1 per factor
    factor_weights: Dict[str, float] = field(default_factory=dict)  # weights used
    reasons: List[str] = field(default_factory=list)


@dataclass
class SignalQualityResult:
    grade: str                           # A+ | A | B+ | B | C | Reject
    quality_score: float                 # 0-100
    rejected: bool
    reasons: List[str] = field(default_factory=list)


@dataclass
class RegimeResult:
    regime: str
    confidence: str
    regime_score: float                  # 0-100
    reasons: List[str] = field(default_factory=list)


@dataclass
class RiskResult:
    entry: Optional[float]
    stop_loss: Optional[float]
    trailing_stop_loss: Optional[float]
    target_1: Optional[float]
    target_2: Optional[float]
    target_3: Optional[float]
    risk_reward_ratio: Optional[float]
    position_score: float                # 0-100
    reasons: List[str] = field(default_factory=list)


@dataclass
class PatternMatch:
    pattern_key: str
    historical_win_pct: Optional[float]
    historical_loss_pct: Optional[float]
    average_target_pct: Optional[float]
    average_sl_pct: Optional[float]
    sample_size: int
    pattern_confidence: str
    reasons: List[str] = field(default_factory=list)


@dataclass
class Explanation:
    summary: str
    decision: str                        # "SELECTED" | "REJECTED" | "WATCH"
    reasons: Dict[str, List[str]] = field(default_factory=dict)


@dataclass
class QuantResult:
    symbol: str
    direction: str
    decision: str                        # "SELECTED" | "REJECTED" | "WATCH"
    probability: ProbabilityResult
    signal_quality: SignalQualityResult
    regime: RegimeResult
    risk: RiskResult
    pattern: PatternMatch
    explanation: Explanation
    processing_ms: float = 0.0
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
