"""
pattern/matcher.py -- Historical Pattern Engine.

Turns today's setup into a coarse, bucketized "pattern key" (so similar
-- not identical -- setups match each other), looks up historical
outcomes for that exact key in local storage, and falls back to a
looser prefix match when there's no exact history yet.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..models.result import PatternMatch, RegimeResult
from ..models.signal import Signal
from .statistics import summarize
from .storage import PatternStorage


def _bucket(value: Optional[float], step: float) -> str:
    if value is None:
        return "na"
    return str(round(round(value / step) * step, 2))


def build_pattern_key(signal: Signal, regime: RegimeResult, precision_step: float = 5.0) -> str:
    """Bucketized signature: direction | regime | trend | ADX bucket | ATR% bucket."""
    atr_pct = None
    if signal.atr is not None and signal.spot_price:
        atr_pct = (signal.atr / signal.spot_price) * 100

    parts = [
        (signal.direction or "NA").upper(),
        regime.regime.replace(" ", "_"),
        (signal.trend or "NA").upper(),
        _bucket(signal.adx, precision_step),
        _bucket(atr_pct, 0.25),
        (signal.candle_structure or "na").lower(),
    ]
    return "|".join(parts)


class PatternMatcher:
    def __init__(self, config: Dict[str, Any]) -> None:
        pattern_cfg = config.get("pattern", {})
        self._min_sample = pattern_cfg.get("min_sample_size_for_confidence", 8)
        self._max_returned = pattern_cfg.get("max_patterns_returned", 5)
        storage_path = pattern_cfg.get("storage_path", "data/patterns.db")
        self._storage = PatternStorage(storage_path)

    @property
    def storage(self) -> PatternStorage:
        return self._storage

    def match(self, signal: Signal, regime: RegimeResult) -> PatternMatch:
        pattern_key = build_pattern_key(signal, regime)
        outcomes = self._storage.fetch_outcomes(pattern_key)
        reasons: List[str] = []

        if not outcomes:
            # Fall back to a coarser prefix (direction|regime|trend) match.
            prefix = "|".join(pattern_key.split("|")[:3])
            similar_keys = self._storage.top_similar_keys(prefix, limit=self._max_returned)
            pooled: List[Dict[str, Any]] = []
            for key in similar_keys:
                pooled.extend(self._storage.fetch_outcomes(key))
            outcomes = pooled
            if outcomes:
                reasons.append(
                    f"No exact historical match for '{pattern_key}'; pooled {len(similar_keys)} "
                    f"similar setup(s) sharing direction/regime/trend."
                )
            else:
                reasons.append("No historical setups recorded yet for this pattern.")

        stats = summarize(outcomes, self._min_sample)

        if stats["sample_size"] > 0:
            reasons.append(
                f"Sample of {stats['sample_size']} historical setup(s): "
                f"{stats['win_pct']}% win / {stats['loss_pct']}% loss."
            )

        return PatternMatch(
            pattern_key=pattern_key,
            historical_win_pct=stats["win_pct"],
            historical_loss_pct=stats["loss_pct"],
            average_target_pct=stats["avg_target_pct"],
            average_sl_pct=stats["avg_sl_pct"],
            sample_size=stats["sample_size"],
            pattern_confidence=stats["confidence"],
            reasons=reasons,
        )
