"""
risk/targets.py -- Dynamic target computation.

Targets are ATR-multiple based, then adjusted by:
  - momentum (stronger momentum -> slightly wider targets)
  - IV (very high IV -> slightly dampened targets, since option premium
    decay/whipsaw risk rises with IV)
  - trend regime is applied by risk/engine.py, which calls this with an
    already-regime-adjusted multiplier set.
"""

from __future__ import annotations

from typing import List, Optional, Tuple


def compute_targets(
    entry: float,
    atr: float,
    direction: str,
    base_multipliers: List[float],
    *,
    momentum_pct: Optional[float] = None,
    iv: Optional[float] = None,
    iv_dampening: float = 0.15,
) -> List[float]:
    """Returns [target_1, target_2, target_3] absolute price levels."""
    sign = 1 if direction.upper() == "CE" else -1

    momentum_boost = 0.0
    if momentum_pct is not None:
        momentum_boost = max(-0.15, min(0.25, momentum_pct / 100 * 2))

    iv_shrink = 0.0
    if iv is not None and iv > 35:
        iv_shrink = min(0.3, ((iv - 35) / 100) * iv_dampening * 4)

    adjustment = 1.0 + momentum_boost - iv_shrink
    adjustment = max(0.5, adjustment)  # never collapse targets below half of base

    targets = []
    for mult in base_multipliers:
        distance = atr * mult * adjustment
        targets.append(round(entry + sign * distance, 2))
    return targets


def risk_reward_ratio(entry: float, stop_loss: float, target_1: float) -> Optional[float]:
    risk = abs(entry - stop_loss)
    reward = abs(target_1 - entry)
    if risk <= 0:
        return None
    return round(reward / risk, 2)
