"""
risk/engine.py -- Dynamic Risk Engine.

Computes entry, stop loss, trailing stop, three targets, risk/reward
ratio, and a 0-100 position score, all adapted from ATR/volatility/
momentum/trend/IV -- never fixed pip/point distances.
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..models.result import RegimeResult, RiskResult
from ..models.signal import Signal
from .targets import compute_targets, risk_reward_ratio
from .trailing import initial_trailing_stop


_REGIME_SL_MULTIPLIER_ADJUST = {
    "Strong Trend": 0.9,     # trends need less room, they don't often retest
    "Trending": 1.0,
    "Range": 1.2,            # ranges chop more, give the stop more room
    "Sideways": 1.3,
    "High Volatility": 1.4,
    "Low Volatility": 0.85,
    "Gap Up": 1.2,
    "Gap Down": 1.2,
    "Expiry": 0.8,           # tighter stops, theta risk dominates
    "News Driven": 1.5,
    "High Volume": 1.0,
    "Low Volume": 1.1,
}


class RiskEngine:
    def __init__(self, config: Dict[str, Any]) -> None:
        self._cfg = config.get("risk", {})

    def evaluate(self, signal: Signal, regime: RegimeResult) -> RiskResult:
        reasons: List[str] = []
        cfg = self._cfg

        entry = signal.option_price or signal.spot_price
        atr = signal.atr
        direction = (signal.direction or "CE").upper()

        if entry is None or atr is None or atr <= 0:
            reasons.append("Insufficient price/ATR data to compute risk levels.")
            return RiskResult(
                entry=entry,
                stop_loss=None,
                trailing_stop_loss=None,
                target_1=None,
                target_2=None,
                target_3=None,
                risk_reward_ratio=None,
                position_score=0.0,
                reasons=reasons,
            )

        sl_multiplier = cfg.get("atr_sl_multiplier", 1.1)
        regime_adjust = _REGIME_SL_MULTIPLIER_ADJUST.get(regime.regime, 1.0)
        effective_sl_multiplier = sl_multiplier * regime_adjust
        reasons.append(
            f"Stop-loss ATR multiplier {sl_multiplier} adjusted x{regime_adjust} for '{regime.regime}' regime."
        )

        sign = 1 if direction == "CE" else -1
        sl_distance = atr * effective_sl_multiplier

        min_distance_pct = cfg.get("min_stop_distance_pct", 0.15) / 100
        min_distance = entry * min_distance_pct
        if sl_distance < min_distance:
            sl_distance = min_distance
            reasons.append(f"Stop distance floored at {min_distance_pct*100:.2f}% of entry to avoid a too-tight stop.")

        stop_loss = round(entry - sign * sl_distance, 2)

        base_multipliers = cfg.get("atr_target_multipliers", [1.5, 2.5, 4.0])
        targets = compute_targets(
            entry=entry,
            atr=atr,
            direction=direction,
            base_multipliers=base_multipliers,
            momentum_pct=signal.momentum_pct,
            iv=signal.iv,
            iv_dampening=cfg.get("iv_target_dampening", 0.15),
        )
        target_1, target_2, target_3 = (targets + [None, None, None])[:3]

        rr = risk_reward_ratio(entry, stop_loss, target_1) if target_1 is not None else None
        if rr is not None:
            reasons.append(f"Risk/Reward to Target 1 is {rr:.2f}.")

        trailing = initial_trailing_stop(entry, stop_loss)

        position_score = self._position_score(rr, regime, signal)
        reasons.append(f"Position score {position_score:.1f}/100 reflects RR, regime fit, and volatility.")

        return RiskResult(
            entry=round(entry, 2),
            stop_loss=stop_loss,
            trailing_stop_loss=trailing,
            target_1=target_1,
            target_2=target_2,
            target_3=target_3,
            risk_reward_ratio=rr,
            position_score=position_score,
            reasons=reasons,
        )

    def _position_score(self, rr, regime: RegimeResult, signal: Signal) -> float:
        score = 0.0
        if rr is not None:
            score += min(50.0, rr * 20)  # RR of 2.5 -> 50 pts
        score += regime.regime_score * 0.3
        if signal.atr and signal.spot_price:
            atr_pct = signal.atr / signal.spot_price * 100
            # Reward moderate volatility (0.4%-1.0%), penalize extremes.
            if 0.4 <= atr_pct <= 1.0:
                score += 20
            elif atr_pct < 0.4:
                score += 10
            else:
                score += 5
        return round(min(100.0, score), 2)
