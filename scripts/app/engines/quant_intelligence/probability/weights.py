"""
probability/weights.py -- Adaptive weight management.

Per the spec: "Never use fixed weights. Use adaptive scoring." Weights
start from config/settings.yaml's `probability.seed_weights` but from
then on live in a small persisted JSON file (learning.weights_path) and
are nudged after every recorded trade outcome by the learning engine
(see learning/updater.py). This module owns:

  - loading the current weight vector (seed on first run)
  - normalizing / clamping so weights always sum to 1.0 within
    [min_weight, max_weight] per factor
  - applying a bounded nudge given a per-factor "was this factor right"
    signal

This is intentionally rule-based (exponential moving average nudge),
not a gradient/ML framework, per the spec's "no ML framework required".
"""

from __future__ import annotations

import json
import os
import threading
from typing import Dict, Optional

from ..utils.exceptions import StorageError


class AdaptiveWeightManager:
    def __init__(
        self,
        seed_weights: Dict[str, float],
        storage_path: str,
        *,
        learning_rate: float = 0.03,
        min_weight: float = 0.01,
        max_weight: float = 0.30,
    ) -> None:
        self._seed_weights = dict(seed_weights)
        self._path = storage_path
        self._learning_rate = learning_rate
        self._min_weight = min_weight
        self._max_weight = max_weight
        self._lock = threading.Lock()
        self._weights: Dict[str, float] = {}
        self._load()

    # -- persistence ---------------------------------------------------------
    def _load(self) -> None:
        with self._lock:
            if os.path.isfile(self._path):
                try:
                    with open(self._path, "r", encoding="utf-8") as fh:
                        data = json.load(fh)
                    weights = data.get("weights", {})
                    # Merge: any new factor added to seed since last persisted
                    # run gets its seed value; anything removed is dropped.
                    merged = {k: weights.get(k, v) for k, v in self._seed_weights.items()}
                    self._weights = self._normalize(merged)
                except (json.JSONDecodeError, OSError) as exc:
                    raise StorageError(
                        f"Failed to read adaptive weights file: {exc}",
                        context={"path": self._path},
                    ) from exc
            else:
                self._weights = self._normalize(dict(self._seed_weights))
                self._save_locked()

    def _save_locked(self) -> None:
        os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
        tmp_path = f"{self._path}.tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as fh:
                json.dump({"weights": self._weights}, fh, indent=2, sort_keys=True)
            os.replace(tmp_path, self._path)
        except OSError as exc:
            raise StorageError(
                f"Failed to persist adaptive weights: {exc}", context={"path": self._path}
            ) from exc

    # -- math ------------------------------------------------------------------
    def _normalize(self, weights: Dict[str, float]) -> Dict[str, float]:
        clamped = {k: max(self._min_weight, min(self._max_weight, v)) for k, v in weights.items()}
        total = sum(clamped.values()) or 1.0
        return {k: v / total for k, v in clamped.items()}

    def get_weights(self) -> Dict[str, float]:
        with self._lock:
            return dict(self._weights)

    def nudge(self, factor_deltas: Dict[str, float]) -> Dict[str, float]:
        """Apply a bounded EMA-style nudge per factor.

        `factor_deltas` values should be in roughly [-1, 1]: positive
        means "this factor's read was aligned with a winning outcome",
        negative means it was aligned with a losing outcome. Unmentioned
        factors are left untouched before re-normalization.
        """
        with self._lock:
            updated = dict(self._weights)
            for factor, delta in factor_deltas.items():
                if factor not in updated:
                    continue
                bounded_delta = max(-1.0, min(1.0, delta))
                updated[factor] = updated[factor] * (1 + self._learning_rate * bounded_delta)
            self._weights = self._normalize(updated)
            self._save_locked()
            return dict(self._weights)

    def reset(self) -> Dict[str, float]:
        with self._lock:
            self._weights = self._normalize(dict(self._seed_weights))
            self._save_locked()
            return dict(self._weights)
