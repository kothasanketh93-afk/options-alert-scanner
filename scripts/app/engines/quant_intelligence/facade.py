"""
facade.py -- QuantEngine: the one integration point.

    from quant_intelligence import QuantEngine
    result = QuantEngine.process(signal)

`QuantEngine` is a thin, thread-safe, lazily-initialized singleton
wrapper around all the sub-engines. It is deliberately a classmethod-
based facade (rather than requiring callers to construct/wire engines
themselves) so the host scanner's later integration is exactly the
two-line snippet in the spec -- no DI container to configure, no
sub-engine imports needed at the call site.

Internally it *is* built with dependency injection (see
QuantEngineImpl.__init__) so tests can construct isolated instances
with temp storage paths instead of touching the module-level default
singleton / its on-disk data/ files.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional

from .cache.cache import TTLCache
from .explainability.engine import ExplainabilityEngine
from .learning.engine import LearningEngine
from .models.result import (
    Explanation,
    PatternMatch,
    ProbabilityResult,
    QuantResult,
    RegimeResult,
    RiskResult,
    SignalQualityResult,
)
from .models.signal import Signal
from .pattern.matcher import PatternMatcher, build_pattern_key
from .probability.engine import ProbabilityEngine
from .probability.weights import AdaptiveWeightManager
from .regime.detector import RegimeDetector
from .risk.engine import RiskEngine
from .signal_quality.engine import SignalQualityEngine
from .utils.config_loader import is_enabled, load_config
from .utils.exceptions import EngineError, ValidationError
from .utils.logging_utils import get_logger

_LOG = get_logger("facade")


def _neutral_probability() -> ProbabilityResult:
    return ProbabilityResult(probability=0.0, confidence="VERY LOW", reasons=["Probability engine disabled or failed."])


def _neutral_quality() -> SignalQualityResult:
    return SignalQualityResult(grade="Reject", quality_score=0.0, rejected=True, reasons=["Signal quality engine disabled or failed."])


def _neutral_regime() -> RegimeResult:
    return RegimeResult(regime="Unknown", confidence="VERY LOW", regime_score=0.0, reasons=["Regime engine disabled or failed."])


def _neutral_risk() -> RiskResult:
    return RiskResult(
        entry=None, stop_loss=None, trailing_stop_loss=None, target_1=None, target_2=None,
        target_3=None, risk_reward_ratio=None, position_score=0.0,
        reasons=["Risk engine disabled or failed."],
    )


def _neutral_pattern(pattern_key: str = "unknown") -> PatternMatch:
    return PatternMatch(
        pattern_key=pattern_key, historical_win_pct=None, historical_loss_pct=None,
        average_target_pct=None, average_sl_pct=None, sample_size=0,
        pattern_confidence="NONE", reasons=["Pattern engine disabled or failed."],
    )


class QuantEngineImpl:
    """The real, injectable implementation. `QuantEngine` (below) wraps a
    lazily-built default instance of this for the simple two-line API."""

    def __init__(self, config: Optional[Dict[str, Any]] = None, config_path: Optional[str] = None) -> None:
        self.config: Dict[str, Any] = config if config is not None else load_config(config_path)

        cache_cfg = self.config.get("cache", {})
        self.cache = TTLCache(
            max_entries=cache_cfg.get("max_entries", 5000),
            default_ttl_seconds=cache_cfg.get("default_ttl_seconds", 30),
        )

        prob_cfg = self.config.get("probability", {})
        learning_cfg = self.config.get("learning", {})
        self.weight_manager = AdaptiveWeightManager(
            seed_weights=prob_cfg.get("seed_weights", {}),
            storage_path=learning_cfg.get("weights_path", "data/adaptive_weights.json"),
            learning_rate=prob_cfg.get("learning_rate", 0.03),
            min_weight=prob_cfg.get("min_weight", 0.01),
            max_weight=prob_cfg.get("max_weight", 0.30),
        )

        self.probability_engine = ProbabilityEngine(self.config, self.weight_manager)
        self.signal_quality_engine = SignalQualityEngine(self.config)
        self.regime_detector = RegimeDetector(self.config)
        self.risk_engine = RiskEngine(self.config)
        self.pattern_matcher = PatternMatcher(self.config)
        self.learning_engine = LearningEngine(self.config, self.weight_manager, self.pattern_matcher)
        self.explainability_engine = ExplainabilityEngine()

        self._lock = threading.Lock()

    # -- main entry point --------------------------------------------------------
    def process(self, signal: Signal) -> QuantResult:
        start = time.perf_counter()
        errors = []

        if not isinstance(signal, Signal):
            raise ValidationError("process() requires a quant_intelligence.models.signal.Signal instance.")
        if not signal.symbol or not signal.direction:
            raise ValidationError("Signal.symbol and Signal.direction are required.")
        if signal.direction.upper() not in ("CE", "PE"):
            raise ValidationError(f"Signal.direction must be 'CE' or 'PE', got {signal.direction!r}.")

        cache_key = self._cache_key(signal)
        if is_enabled(self.config, "cache"):
            cached = self.cache.get(cache_key)
            if cached is not None:
                return cached

        probability = _neutral_probability()
        if is_enabled(self.config, "probability"):
            try:
                probability = self.probability_engine.score(signal)
            except Exception as exc:  # noqa: BLE001 - isolate sub-engine failures
                errors.append(f"probability engine failed: {exc}")
                _LOG.exception("probability engine failed")

        regime = _neutral_regime()
        if is_enabled(self.config, "regime"):
            try:
                regime = self.regime_detector.detect(signal)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"regime engine failed: {exc}")
                _LOG.exception("regime engine failed")

        risk = _neutral_risk()
        if is_enabled(self.config, "risk"):
            try:
                risk = self.risk_engine.evaluate(signal, regime)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"risk engine failed: {exc}")
                _LOG.exception("risk engine failed")

        signal_quality = _neutral_quality()
        if is_enabled(self.config, "signal_quality"):
            try:
                signal_quality = self.signal_quality_engine.evaluate(signal, probability, risk.risk_reward_ratio)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"signal quality engine failed: {exc}")
                _LOG.exception("signal quality engine failed")

        pattern_key = build_pattern_key(signal, regime)
        pattern = _neutral_pattern(pattern_key)
        if is_enabled(self.config, "pattern"):
            try:
                pattern = self.pattern_matcher.match(signal, regime)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"pattern engine failed: {exc}")
                _LOG.exception("pattern engine failed")

        explanation = Explanation(summary="Explainability engine disabled or failed.", decision="WATCH", reasons={})
        if is_enabled(self.config, "explainability"):
            try:
                explanation = self.explainability_engine.build(
                    symbol=signal.symbol,
                    direction=signal.direction,
                    probability=probability,
                    signal_quality=signal_quality,
                    regime=regime,
                    risk=risk,
                    pattern=pattern,
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"explainability engine failed: {exc}")
                _LOG.exception("explainability engine failed")

        elapsed_ms = round((time.perf_counter() - start) * 1000, 3)

        result = QuantResult(
            symbol=signal.symbol,
            direction=signal.direction,
            decision=explanation.decision,
            probability=probability,
            signal_quality=signal_quality,
            regime=regime,
            risk=risk,
            pattern=pattern,
            explanation=explanation,
            processing_ms=elapsed_ms,
            errors=errors,
        )

        if is_enabled(self.config, "cache"):
            ttl = self.config.get("cache", {}).get("default_ttl_seconds", 30)
            self.cache.set(cache_key, result, ttl_seconds=ttl)

        return result

    def record_outcome(self, result: QuantResult, **kwargs) -> Dict[str, Any]:
        if not is_enabled(self.config, "learning"):
            return {"weights": self.weight_manager.get_weights(), "skipped": True}
        try:
            return self.learning_engine.record_outcome(result, **kwargs)
        except Exception as exc:  # noqa: BLE001
            raise EngineError(f"learning engine failed to record outcome: {exc}") from exc

    def _cache_key(self, signal: Signal) -> str:
        # Round generated_at to the second so identical inputs within the
        # same second reuse a cache entry, but re-scoring a minute later
        # (fresh candle data) is treated as a distinct request.
        return f"{signal.symbol}|{signal.direction}|{int(signal.generated_at)}|{signal.strike}"


class QuantEngine:
    """Static facade -- see module docstring. Lazily builds one shared
    QuantEngineImpl using the default config/settings.yaml on first use."""

    _instance: Optional[QuantEngineImpl] = None
    _lock = threading.Lock()

    @classmethod
    def _get_instance(cls) -> QuantEngineImpl:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = QuantEngineImpl()
        return cls._instance

    @classmethod
    def configure(cls, config: Optional[Dict[str, Any]] = None, config_path: Optional[str] = None) -> None:
        """Explicitly (re)build the shared instance with custom config.
        Optional -- only needed to override config/settings.yaml defaults."""
        with cls._lock:
            cls._instance = QuantEngineImpl(config=config, config_path=config_path)

    @classmethod
    def process(cls, signal: Signal) -> QuantResult:
        return cls._get_instance().process(signal)

    @classmethod
    def record_outcome(cls, result: QuantResult, **kwargs) -> Dict[str, Any]:
        return cls._get_instance().record_outcome(result, **kwargs)
