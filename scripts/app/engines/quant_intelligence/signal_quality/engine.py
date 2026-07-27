"""
signal_quality/engine.py -- Signal Quality Engine.

Produces a letter grade (A+, A, B+, B, C, Reject) from the probability
result plus a battery of independent quality checks. Any hard-fail
check (false breakout, poor liquidity, poor risk/reward, low
confidence, weak momentum/volume, late entry) forces a Reject
regardless of the raw quality score -- grade is a ceiling, not just a
threshold lookup.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..models.result import ProbabilityResult, SignalQualityResult
from ..models.signal import Signal
from . import validator as v


class SignalQualityEngine:
    def __init__(self, config: Dict[str, Any]) -> None:
        self._cfg = config.get("signal_quality", {})
        thresholds = self._cfg.get(
            "grade_thresholds", {"A+": 88, "A": 78, "B+": 68, "B": 58, "C": 45}
        )
        # Sort descending by score so first match wins.
        self._thresholds = sorted(thresholds.items(), key=lambda kv: kv[1], reverse=True)

    def _grade_from_score(self, score: float) -> str:
        for label, threshold in self._thresholds:
            if score >= threshold:
                return label
        return "Reject"

    def evaluate(
        self,
        signal: Signal,
        probability: ProbabilityResult,
        risk_reward_ratio: Optional[float],
    ) -> SignalQualityResult:
        reasons: List[str] = []
        hard_fail = False

        checks = [
            v.check_late_entry(signal, self._cfg),
            v.check_momentum(signal, self._cfg),
            v.check_volume(signal, self._cfg),
            v.check_liquidity(signal, self._cfg),
            v.check_false_breakout(signal, self._cfg),
            v.check_risk_reward(risk_reward_ratio, self._cfg),
            v.check_confidence(probability.probability, self._cfg),
        ]
        for passed, reason in checks:
            if not passed:
                hard_fail = True
                if reason:
                    reasons.append(reason)

        # Quality score blends probability with how many soft checks passed.
        passed_count = sum(1 for passed, _ in checks if passed)
        pass_ratio = passed_count / len(checks)
        quality_score = round(0.7 * probability.probability + 0.3 * (pass_ratio * 100), 2)

        if hard_fail:
            grade = "Reject"
        else:
            grade = self._grade_from_score(quality_score)
            reasons.append(f"Quality score {quality_score:.1f} met the '{grade}' threshold.")

        return SignalQualityResult(
            grade=grade,
            quality_score=quality_score,
            rejected=(grade == "Reject"),
            reasons=reasons,
        )
