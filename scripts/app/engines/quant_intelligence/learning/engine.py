"""
learning/engine.py -- Learning Engine.

The single write-path for "what actually happened" to a previously
scored signal. Called by the host application later (once trades are
closed) via `QuantEngine.record_outcome(...)` -- NOT during
`QuantEngine.process()`, which is read-only/side-effect-free apart from
cache writes. This keeps `process()` safe to call speculatively/
repeatedly without polluting learning data.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

from ..models.result import QuantResult
from ..pattern.matcher import PatternMatcher
from ..probability.weights import AdaptiveWeightManager
from .history import TradeHistoryStore
from .updater import compute_factor_deltas


class LearningEngine:
    def __init__(
        self,
        config: Dict[str, Any],
        weight_manager: AdaptiveWeightManager,
        pattern_matcher: PatternMatcher,
    ) -> None:
        learning_cfg = config.get("learning", {})
        self._history = TradeHistoryStore(
            learning_cfg.get("history_path", "data/trade_history.jsonl"),
            max_records=learning_cfg.get("max_history_records", 20000),
        )
        self._weight_manager = weight_manager
        self._pattern_matcher = pattern_matcher

    def record_outcome(
        self,
        result: QuantResult,
        *,
        outcome: str,                     # "WIN" | "LOSS" | "BREAKEVEN"
        exit_price: Optional[float] = None,
        pnl: Optional[float] = None,
        duration_seconds: Optional[float] = None,
        sl_hit: Optional[bool] = None,
        target_hit: Optional[str] = None,  # "T1" | "T2" | "T3" | None
    ) -> Dict[str, Any]:
        """Persist the realized outcome, nudge adaptive weights, and update
        the pattern database. Returns the new weight vector for visibility."""
        outcome = outcome.upper()

        entry = result.risk.entry
        target_pct = None
        sl_pct = None
        if entry:
            if outcome == "WIN" and exit_price is not None:
                target_pct = round(abs(exit_price - entry) / entry * 100, 3)
            if outcome == "LOSS" and exit_price is not None:
                sl_pct = round(abs(exit_price - entry) / entry * 100, 3)

        # 1) history log (full audit trail)
        self._history.append(
            {
                "symbol": result.symbol,
                "direction": result.direction,
                "decision": result.decision,
                "probability": result.probability.probability,
                "confidence": result.probability.confidence,
                "signal_quality_grade": result.signal_quality.grade,
                "regime": result.regime.regime,
                "pattern_key": result.pattern.pattern_key,
                "risk": {
                    "entry": result.risk.entry,
                    "stop_loss": result.risk.stop_loss,
                    "target_1": result.risk.target_1,
                    "target_2": result.risk.target_2,
                    "target_3": result.risk.target_3,
                    "risk_reward_ratio": result.risk.risk_reward_ratio,
                },
                "outcome": outcome,
                "exit_price": exit_price,
                "pnl": pnl,
                "duration_seconds": duration_seconds,
                "sl_hit": sl_hit,
                "target_hit": target_hit,
                "recorded_at": time.time(),
            }
        )

        # 2) pattern database
        self._pattern_matcher.storage.record_outcome(
            pattern_key=result.pattern.pattern_key,
            symbol=result.symbol,
            direction=result.direction,
            outcome=outcome,
            target_pct=target_pct,
            sl_pct=sl_pct,
        )

        # 3) adaptive weights
        deltas = compute_factor_deltas(result.probability.factor_scores, outcome)
        new_weights = self._weight_manager.nudge(deltas) if deltas else self._weight_manager.get_weights()

        return {"weights": new_weights, "pattern_key": result.pattern.pattern_key}

    def recent_history(self, limit: int = 200):
        return self._history.recent(limit)
