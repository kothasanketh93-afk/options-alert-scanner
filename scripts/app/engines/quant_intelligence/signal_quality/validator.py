"""
signal_quality/validator.py -- Individual quality checks.

Each function returns (passed: bool, reason: Optional[str]). A reason
is only populated on failure, ready to be surfaced verbatim in
rejection explanations ("Late entries", "Weak momentum", "Weak
volume", "Poor liquidity", "False breakout", "Poor risk reward", "Low
confidence" per the spec).
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional, Tuple

from ..models.signal import Signal


def check_late_entry(signal: Signal, cfg: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    max_lag = cfg.get("max_entry_lag_minutes", 12)
    age_minutes = (time.time() - signal.generated_at) / 60.0
    if age_minutes > max_lag:
        return False, f"Late entry: signal is {age_minutes:.1f} min old (limit {max_lag} min)."
    return True, None


def check_momentum(signal: Signal, cfg: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    min_momentum = cfg.get("min_momentum_pct", 0.05)
    if signal.momentum_pct is None:
        return False, "Weak momentum: momentum data unavailable."
    is_ce = bool(signal.direction and signal.direction.upper() == "CE")
    signed = signal.momentum_pct if is_ce else -signal.momentum_pct
    if signed < min_momentum:
        return False, f"Weak momentum: {signed:.3f}% below minimum {min_momentum}%."
    return True, None


def check_volume(signal: Signal, cfg: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    min_ratio = cfg.get("min_volume_ratio", 0.6)
    if signal.volume is None or not signal.min_volume_threshold:
        return False, "Weak volume: volume/threshold data unavailable."
    ratio = signal.volume / signal.min_volume_threshold
    if ratio < min_ratio:
        return False, f"Weak volume: {ratio:.2f}x minimum threshold (need {min_ratio:.2f}x)."
    return True, None


def check_liquidity(signal: Signal, cfg: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    min_ratio = cfg.get("min_liquidity_ratio", 0.6)
    if signal.oi is None or not signal.min_oi_threshold:
        return False, "Poor liquidity: OI/threshold data unavailable."
    ratio = signal.oi / signal.min_oi_threshold
    spread_ok = signal.spread_pct is None or signal.spread_pct <= 1.5
    if ratio < min_ratio or not spread_ok:
        return False, f"Poor liquidity: OI ratio {ratio:.2f}x, spread {signal.spread_pct}."
    return True, None


def check_false_breakout(signal: Signal, cfg: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    if signal.breakout_strength is None:
        return True, None  # no breakout claimed -> nothing to invalidate
    if signal.breakout_strength < 0.3 and (signal.volume or 0) and signal.avg_volume:
        if signal.volume < signal.avg_volume:
            return False, "False breakout: low breakout strength unconfirmed by volume."
    return True, None


def check_risk_reward(risk_reward_ratio: Optional[float], cfg: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    min_rr = cfg.get("min_risk_reward", 1.3)
    if risk_reward_ratio is None:
        return False, "Poor risk reward: risk/reward could not be computed."
    if risk_reward_ratio < min_rr:
        return False, f"Poor risk reward: {risk_reward_ratio:.2f} below minimum {min_rr:.2f}."
    return True, None


def check_confidence(probability: float, cfg: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    min_probability = cfg.get("min_probability_for_grade", 40)
    if probability < min_probability:
        return False, f"Low confidence: probability {probability:.1f} below minimum {min_probability}."
    return True, None
