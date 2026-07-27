"""
explainability/engine.py -- Explainability Engine.

Assembles every sub-engine's `reasons` into one structured Explanation
(grouped by dimension, for programmatic consumers) plus a short human
-readable summary paragraph (for Telegram/dashboard display).
"""

from __future__ import annotations

from typing import Any, Dict

from ..models.result import (
    Explanation,
    PatternMatch,
    ProbabilityResult,
    RegimeResult,
    RiskResult,
    SignalQualityResult,
)


class ExplainabilityEngine:
    def build(
        self,
        *,
        symbol: str,
        direction: str,
        probability: ProbabilityResult,
        signal_quality: SignalQualityResult,
        regime: RegimeResult,
        risk: RiskResult,
        pattern: PatternMatch,
    ) -> Explanation:
        if signal_quality.rejected:
            decision = "REJECTED"
        elif signal_quality.grade in ("A+", "A"):
            decision = "SELECTED"
        else:
            decision = "WATCH"

        reasons: Dict[str, Any] = {
            "probability": probability.reasons,
            "signal_quality": signal_quality.reasons,
            "regime": regime.reasons,
            "risk": risk.reasons,
            "pattern": pattern.reasons,
        }

        summary = self._summary(
            decision, symbol, direction, probability, signal_quality, regime, risk, pattern
        )

        return Explanation(summary=summary, decision=decision, reasons=reasons)

    def _summary(
        self,
        decision: str,
        symbol: str,
        direction: str,
        probability: ProbabilityResult,
        signal_quality: SignalQualityResult,
        regime: RegimeResult,
        risk: RiskResult,
        pattern: PatternMatch,
    ) -> str:
        parts = [
            f"{symbol} {direction}: {decision} (grade {signal_quality.grade}, "
            f"probability {probability.probability:.1f}% [{probability.confidence}])."
        ]
        parts.append(f"Regime: {regime.regime} ({regime.confidence}).")
        if risk.risk_reward_ratio is not None:
            parts.append(f"Risk/Reward {risk.risk_reward_ratio:.2f} with entry {risk.entry} / SL {risk.stop_loss}.")
        if pattern.sample_size:
            parts.append(
                f"Historical pattern win rate {pattern.historical_win_pct}% over {pattern.sample_size} sample(s)."
            )
        else:
            parts.append("No historical pattern sample yet.")
        if signal_quality.rejected:
            top_reason = signal_quality.reasons[0] if signal_quality.reasons else "quality checks failed."
            parts.append(f"Primary rejection reason: {top_reason}")
        return " ".join(parts)
