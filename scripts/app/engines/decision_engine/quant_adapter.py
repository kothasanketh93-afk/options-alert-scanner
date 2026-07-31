"""
app/engines/decision_engine/quant_adapter.py -- Part 11 integration glue.
------------------------------------------------------------------------------
Wires the independent Quant Intelligence Engine
(app/engines/quant_intelligence/) into the existing, unmodified
Decision Engine as its FINAL step. `attach()` is called once from
engine.py's evaluate(), AFTER the Standard Decision Object has already
been fully assembled by the existing confidence/probability/risk/
entry_validation/stop_loss/target/trade_quality/explanation modules --
per the merge spec: "After all existing decision logic completes,
invoke QuantEngine.process(signal)."

DISCOVERY, NOT DUPLICATION (same rule as adapters.py): this module
reads fields the upstream engines already attached to the trade dict
this cycle (options_intelligence's Standard Output Object, incl. its
Part-11-added `raw` sub-dict; app/market/snapshot.py's already-computed
regime/EMA/VWAP/ATR read, passed in via `snapshot`; early_breakout's
momentum_score/base_score when present). It never re-fetches a candle,
re-calls a broker, or reruns scoring math that already ran upstream.

FAIL-SOFT BY DESIGN: `attach()` never raises. Any failure -- config
flag off, the quant_intelligence package failing to import, or
QuantEngine.process() itself raising -- degrades to
`decision_object["quant_intelligence"] = {"enabled": False, ...}` and
leaves every other field of the (already-computed) Decision Object
completely untouched. The existing Decision Engine's own approval/
rejection/priority/risk numbers are NEVER lost or replaced because of
a Quant Intelligence failure.

INFLUENCE IS OPT-IN: by default (config.QUANT_INTELLIGENCE_INFLUENCE_
ENABLED = False) Quant Intelligence is computed and attached for
visibility/logging only -- exactly the OPTIONS_INTELLIGENCE_FILTER_
ENABLED / DECISION_ENGINE_FILTER_ENABLED rollout pattern already used
twice elsewhere in this codebase. Existing scanner behavior is
therefore byte-for-byte unchanged out of the box; flipping the flag on
lets a low-probability Quant Intelligence read flip an
otherwise-approved trade to rejected (see `_apply_influence()`) -- it
can only make an approved trade MORE cautious, never override an
already-rejected trade back to approved.

KNOWN INPUT GAPS (documented, not hidden -- see
docs/QUANT_INTELLIGENCE_INTEGRATION.md "Known limitations"): this
codebase deliberately does not compute RSI, market breadth, or a raw
(non-early-breakout) momentum reading anywhere (see scanner.py /
trade_score.py's own docstrings on this). Those Signal fields are
therefore left as None for trades that don't carry a proxy for them,
exactly like every other optional Signal field Quant Intelligence's
own probability engine already treats as "no contribution" rather
than an error (see quant_intelligence/probability/scorer.py) --
nothing here invents a fake reading to fill the gap.

candle_structure is the one exception to the list above: as of
candlestick_patterns.py, this codebase DOES compute a candle-pattern
classification (13 patterns, pure arithmetic on candles already
fetched -- see that module). _candle_structure_proxy() below reads
scanner.score_strike()'s already-attached trade['candlestick_patterns']
and maps its dominant pattern to the exact key
score_candle_structure() expects. Still None whenever Candlestick
Patterns is disabled (config.CANDLESTICK_PATTERNS_ENABLED = False) or
no directional pattern was found this cycle -- same "None means no
contribution" convention as everything else here.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional

from logging_setup import get_logger
from config import (
    QUANT_INTELLIGENCE_ENABLED,
    QUANT_INTELLIGENCE_INFLUENCE_ENABLED,
    QUANT_INTELLIGENCE_MIN_PROBABILITY,
    MIN_OI,
    MIN_OPTION_VOLUME,
)

_logger = get_logger("decision_engine.quant_intelligence")
IST = timezone(timedelta(hours=5, minutes=30))

# Resolved lazily (not at import time) so a broken/missing
# quant_intelligence package never prevents decision_engine itself
# from importing -- see _get_quant_engine().
_QuantEngine = None
_Signal = None
_import_error: Optional[Exception] = None
_import_attempted = False


def _get_quant_engine():
    """Imports app.engines.quant_intelligence exactly once. Returns
    (QuantEngine, Signal) or (None, None) if the package is missing or
    broken -- never raises."""
    global _QuantEngine, _Signal, _import_error, _import_attempted
    if _import_attempted:
        return _QuantEngine, _Signal
    _import_attempted = True
    try:
        from app.engines.quant_intelligence import QuantEngine, Signal
        _QuantEngine, _Signal = QuantEngine, Signal
    except Exception as exc:  # noqa: BLE001 - defensive, see module docstring
        _import_error = exc
        _logger.error(
            f"QUANT_INTELLIGENCE_IMPORT_FAILED: {type(exc).__name__}: {exc}", exc_info=True
        )
    return _QuantEngine, _Signal


def _disabled_result(reason: str) -> Dict[str, Any]:
    return {"enabled": False, "reason": reason}


def _parse_alert_time(trade: dict) -> Optional[float]:
    raw = trade.get("alert_time")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw).timestamp()
    except (TypeError, ValueError):
        return None


def _spread_pct(raw: dict) -> Optional[float]:
    bid, ask = raw.get("bid"), raw.get("ask")
    if bid is None or ask is None:
        return None
    mid = (bid + ask) / 2.0
    if not mid:
        return None
    return round(((ask - bid) / mid) * 100, 3)


def _momentum_pct_proxy(trade: dict, direction: str) -> Optional[float]:
    """Early Breakout's momentum.py already computes a DIRECTION-AWARE
    0-100 'how much this favors the trade's own direction' score
    (ADX/ROC/RSI-slope/MACD-hist/EMA-slope/vol-accel/body%/VWAP-dist
    blended -- see that module) but only the blended momentum_score
    reaches the trade dict, not the raw signed reading. Re-deriving a
    signed proxy from it (rather than leaving it None) lets Quant
    Intelligence's own direction-aware momentum scorer
    (probability/scorer.py score_momentum(), which re-applies the
    direction sign itself) use SOMETHING for early_breakout trades
    without recomputing ADX/ROC/etc. a second time. Only available
    when strategy == 'early_breakout'; None otherwise (plain
    trade_score-path trades have no momentum reading anywhere in this
    codebase -- deliberately, per trade_score.py's own docstring)."""
    if trade.get("strategy") != "early_breakout":
        return None
    momentum_score = trade.get("momentum_score")
    if momentum_score is None:
        return None
    # momentum_score is already "favorable to the trade's own direction"
    # (0-100, 50=neutral). Un-apply that direction-favorability so the
    # value going into Signal.momentum_pct is direction-RAW (positive =
    # bullish move) -- Quant Intelligence's scorer re-applies the sign
    # for direction itself, so double-applying it here would cancel out
    # for PE trades.
    normalized = (float(momentum_score) - 50.0) / 50.0  # -1..1, 0 = neutral
    is_ce = (direction or "").upper() == "CE"
    return (normalized if is_ce else -normalized) * 0.8  # scaled into scorer's expected ballpark


# candlestick_patterns.py's dominant_pattern names ("Bullish Engulfing",
# "Hammer", ...) -> the exact snake_case keys
# quant_intelligence.probability.scorer.score_candle_structure() checks
# for (see that function). Only patterns it explicitly recognizes are
# mapped; anything else (Doji, Spinning Top, Inside/Outside Bar, Three
# White Soldiers/Black Crows) is left unmapped -- score_candle_structure()
# already treats an unrecognized-but-present string as a mild 0.3 neutral
# read rather than 0.0, so passing an unmapped name through as-is (lowercased,
# underscored) is still safe, just less informative than the exact keys below.
_CANDLE_STRUCTURE_KEY_MAP = {
    ("Bullish Engulfing", "bullish"): "bullish_engulfing",
    ("Bearish Engulfing", "bearish"): "bearish_engulfing",
    ("Hammer", "bullish"): "hammer",
    ("Shooting Star", "bearish"): "shooting_star",
    ("Morning Star", "bullish"): "morning_star",
    ("Evening Star", "bearish"): "evening_star",
    ("Marubozu", "bullish"): "marubozu_bullish",
    ("Marubozu", "bearish"): "marubozu_bearish",
}


def _candle_structure_proxy(trade: dict) -> Optional[str]:
    """Reads candlestick_patterns.py's already-computed dominant pattern
    off the trade dict (scanner.score_strike() attaches it at
    trade['candlestick_patterns'] -- see that module) and maps it to
    the key quant_intelligence's score_candle_structure() expects.
    None if Candlestick Patterns is disabled, found nothing this cycle,
    or the dominant pattern has no explicit mapping above."""
    read = trade.get("candlestick_patterns")
    if not read or not read.get("enabled"):
        return None
    pattern, bias = read.get("dominant_pattern"), read.get("dominant_bias")
    if not pattern or not bias:
        return None
    return _CANDLE_STRUCTURE_KEY_MAP.get((pattern, bias))


_SNAPSHOT_REGIME_TO_TREND = {
    "TRENDING_BULLISH": "UP",
    "TRENDING_BEARISH": "DOWN",
    "SIDEWAYS": "FLAT",
    "VOLATILE": "FLAT",
}


def _trend_from_snapshot(snapshot: dict) -> Optional[str]:
    regime = snapshot.get("regime")
    if not regime:
        return None
    return _SNAPSHOT_REGIME_TO_TREND.get(regime, "FLAT")


def _gap_pct_from_snapshot(snapshot: dict) -> Optional[float]:
    gap = snapshot.get("gap") or {}
    gap_pct = gap.get("gap_pct")
    if gap_pct is None:
        return None
    return round(gap_pct * 100, 4)  # snapshot stores a fraction; Signal/regime config use percent


def _expiry_fields(trade: dict) -> tuple:
    """Reuses option_chain.days_to_expiry() (already used by
    scanner.py for the exact same field) rather than re-parsing dates
    here -- 'reuse existing, do not duplicate calculations'. Deferred
    import for the same reason snapshot.py defers market_data: keeps
    this module importable without the broker stack. Returns
    (expiry_days, is_expiry_day), both None if trade['expiry'] is
    missing/malformed."""
    expiry = trade.get("expiry")
    if not expiry:
        return None, None
    try:
        from option_chain import days_to_expiry
        remaining = days_to_expiry(expiry)
        return remaining, (remaining == 0)
    except Exception:  # noqa: BLE001 - never let a date-format surprise break signal building
        return None, None


def build_signal(trade: dict, snapshot: Optional[dict] = None):
    """Build a quant_intelligence.Signal from fields ALREADY on the
    trade dict / snapshot this cycle -- no recomputation, no new
    fetches. Exposed as a standalone function (rather than inlined in
    `attach()`) so tests / a future learning-loop call site
    (tracker.py, see docs/QUANT_INTELLIGENCE_INTEGRATION.md's "Manual
    steps required") can build an equivalent Signal without importing
    this module's private internals."""
    _, Signal = _get_quant_engine()
    if Signal is None:
        raise RuntimeError("quant_intelligence.Signal is unavailable")

    snapshot = snapshot or {}
    direction = trade.get("direction")
    oi_intel = trade.get("options_intelligence") or {}
    raw = oi_intel.get("raw") or {}
    expiry_days, is_expiry_day = _expiry_fields(trade)

    return Signal(
        symbol=trade.get("symbol", "UNKNOWN"),
        direction=direction or "CE",
        generated_at=_parse_alert_time(trade) or time.time(),
        spot_price=trade.get("spot_price"),
        option_price=trade.get("alert_premium"),
        strike=trade.get("strike"),
        expiry_days=expiry_days,
        trend=_trend_from_snapshot(snapshot),
        ema_fast=snapshot.get("ema9"),
        ema_slow=snapshot.get("ema21"),
        vwap=snapshot.get("vwap"),
        rsi=None,   # not computed anywhere in this codebase -- see module docstring
        adx=None,   # only available inside early_breakout.momentum's local scope, not on the trade dict
        atr=snapshot.get("atr"),
        pcr=oi_intel.get("overall_pcr", oi_intel.get("strike_pcr")),
        oi=raw.get("oi"),
        oi_change_pct=raw.get("oi_change_pct"),
        volume=raw.get("volume"),
        avg_volume=None,  # no rolling average volume tracked anywhere upstream
        iv=raw.get("iv"),
        momentum_pct=_momentum_pct_proxy(trade, direction),
        market_breadth=None,   # no market-breadth engine in this codebase
        breakout_strength=(trade["base_score"] / 100.0) if "base_score" in trade else None,
        # Was unconditionally None ("no candlestick-pattern classification
        # engine in this codebase") -- now sourced from candlestick_patterns.py
        # via the trade dict, same "reuse, don't recompute" rule as every
        # other field in this function. Still None whenever Candlestick
        # Patterns is disabled or found nothing this cycle, exactly as before.
        candle_structure=_candle_structure_proxy(trade),
        min_oi_threshold=float(MIN_OI),
        min_volume_threshold=float(MIN_OPTION_VOLUME),
        spread_pct=_spread_pct(raw),
        gap_pct=_gap_pct_from_snapshot(snapshot),
        is_expiry_day=is_expiry_day,
        is_news_driven=None,
    )


def _apply_influence(decision_object: dict, result) -> None:
    """Opt-in only (config.QUANT_INTELLIGENCE_INFLUENCE_ENABLED).
    Can only make an approved trade MORE cautious (flip approved ->
    rejected on a confidently-bad Quant Intelligence read) -- never
    flips an already-rejected trade back to approved, and never
    touches trade_quality_score/confidence_score/risk numbers the
    existing engine already computed."""
    if result.decision != "REJECTED":
        return
    if result.probability.probability >= QUANT_INTELLIGENCE_MIN_PROBABILITY:
        return
    if not decision_object.get("approved", True):
        return  # already rejected by the existing Decision Engine -- nothing to flip

    decision_object["approved"] = False
    decision_object["decision"] = "Trade Rejected"
    reason = (
        f"Quant Intelligence rejected (probability {result.probability.probability:.1f}% "
        f"below floor {QUANT_INTELLIGENCE_MIN_PROBABILITY}%)"
    )
    decision_object.setdefault("rejection_reasons", []).append(reason)
    decision_object.setdefault("warnings", []).append(
        "Decision flipped by Quant Intelligence (config.QUANT_INTELLIGENCE_INFLUENCE_ENABLED)"
    )


def attach(trade: dict, decision_object: dict, snapshot: Optional[dict] = None) -> None:
    """Mutates decision_object in place, adding
    decision_object['quant_intelligence']. Never raises -- see module
    docstring."""
    if not QUANT_INTELLIGENCE_ENABLED:
        decision_object["quant_intelligence"] = _disabled_result(
            "Quant Intelligence disabled (config.QUANT_INTELLIGENCE_ENABLED = False)"
        )
        return

    QuantEngine, _ = _get_quant_engine()
    if QuantEngine is None:
        decision_object["quant_intelligence"] = _disabled_result(
            f"Quant Intelligence unavailable: "
            f"{type(_import_error).__name__ if _import_error else 'import failed'}"
        )
        return

    started = time.monotonic()
    try:
        signal = build_signal(trade, snapshot)
        result = QuantEngine.process(signal)
        elapsed_ms = round((time.monotonic() - started) * 1000, 2)

        decision_object["quant_intelligence"] = {
            "enabled": True,
            "decision": result.decision,
            "probability": result.probability.probability,
            "confidence": result.probability.confidence,
            "signal_quality_grade": result.signal_quality.grade,
            "regime": result.regime.regime,
            "regime_confidence": result.regime.confidence,
            "risk": {
                "entry": result.risk.entry,
                "stop_loss": result.risk.stop_loss,
                "trailing_stop_loss": result.risk.trailing_stop_loss,
                "target_1": result.risk.target_1,
                "target_2": result.risk.target_2,
                "target_3": result.risk.target_3,
                "risk_reward_ratio": result.risk.risk_reward_ratio,
                "position_score": result.risk.position_score,
            },
            "pattern": {
                "pattern_key": result.pattern.pattern_key,
                "historical_win_pct": result.pattern.historical_win_pct,
                "historical_loss_pct": result.pattern.historical_loss_pct,
                "sample_size": result.pattern.sample_size,
                "pattern_confidence": result.pattern.pattern_confidence,
            },
            "explanation": result.explanation.summary,
            "reasons": result.explanation.reasons,
            "errors": result.errors,
            "execution_time_ms": elapsed_ms,
        }

        if QUANT_INTELLIGENCE_INFLUENCE_ENABLED:
            _apply_influence(decision_object, result)

        _logger.info(
            f"QUANT_INTELLIGENCE {trade.get('symbol')} {trade.get('direction')}: "
            f"{result.decision} | Grade={result.signal_quality.grade} "
            f"Probability={result.probability.probability} Regime={result.regime.regime} "
            f"({elapsed_ms}ms)"
        )
        if result.errors:
            _logger.warning(
                f"QUANT_INTELLIGENCE_PARTIAL {trade.get('symbol')} {trade.get('direction')}: "
                f"{result.errors}"
            )

    except Exception as exc:  # noqa: BLE001 - see module docstring: never interrupt the Decision Engine
        _logger.error(
            f"QUANT_INTELLIGENCE_FAILED {trade.get('symbol')} {trade.get('direction')}: "
            f"{type(exc).__name__}: {exc}",
            exc_info=True,
        )
        decision_object["quant_intelligence"] = {
            "enabled": False,
            "error": True,
            "reason": f"Quant Intelligence internal error, ignored: {exc}",
        }


def record_outcome(trade: dict, snapshot: Optional[dict] = None, **outcome_kwargs) -> Optional[Dict[str, Any]]:
    """Optional learning-loop hook -- NOT called automatically anywhere
    in this merge (see docs/QUANT_INTELLIGENCE_INTEGRATION.md "Manual
    steps required": wiring this into tracker.py once a trade reaches
    a terminal status is a deliberate opt-in left to the operator,
    since it means deciding exactly which terminal statuses count as
    WIN/LOSS/BREAKEVEN for this project's specific target/SL semantics).

    Re-derives an equivalent Signal from the trade dict (cheap: Quant
    Intelligence's own result cache makes re-processing an
    already-seen signal within its TTL a cache hit, not a recompute)
    and forwards to QuantEngine.record_outcome(...). Returns the
    engine's response dict, or None if Quant Intelligence is
    disabled/unavailable/fails (logged, never raised).

    Usage (NOT wired in automatically):
        from app.engines.decision_engine import quant_adapter
        quant_adapter.record_outcome(
            trade, outcome="WIN", exit_price=..., pnl=..., duration_seconds=...,
        )
    """
    if not QUANT_INTELLIGENCE_ENABLED:
        return None
    QuantEngine, _ = _get_quant_engine()
    if QuantEngine is None:
        return None
    try:
        signal = build_signal(trade, snapshot)
        result = QuantEngine.process(signal)
        return QuantEngine.record_outcome(result, **outcome_kwargs)
    except Exception as exc:  # noqa: BLE001
        _logger.error(
            f"QUANT_INTELLIGENCE_RECORD_OUTCOME_FAILED {trade.get('symbol')} "
            f"{trade.get('direction')}: {type(exc).__name__}: {exc}",
            exc_info=True,
        )
        return None


def health():
    """Health-check callable for production.health_registry, same
    shape/convention as every other Part 9/10 monitor's `.health()`
    (see e.g. production/scanner_monitor.py). Registered at startup in
    app/backend/api/app.py -- see docs/QUANT_INTELLIGENCE_INTEGRATION.md.
    """
    from production.health_states import ComponentHealth, HealthState

    if not QUANT_INTELLIGENCE_ENABLED:
        return ComponentHealth(
            name="quant_intelligence", state=HealthState.OFFLINE,
            message="disabled via config.QUANT_INTELLIGENCE_ENABLED",
        )

    QuantEngine, Signal = _get_quant_engine()
    if QuantEngine is None:
        return ComponentHealth(
            name="quant_intelligence", state=HealthState.CRITICAL,
            message=f"package import failed: {_import_error}",
        )

    try:
        probe = Signal(symbol="_HEALTHCHECK_", direction="CE", spot_price=100.0, atr=1.0)
        result = QuantEngine.process(probe)
        metrics = {"processing_ms": result.processing_ms, "engine_errors": len(result.errors)}
        if result.errors:
            return ComponentHealth(
                name="quant_intelligence", state=HealthState.DEGRADED,
                message=f"processed with sub-engine errors: {result.errors}", metrics=metrics,
            )
        return ComponentHealth(
            name="quant_intelligence", state=HealthState.HEALTHY,
            message="operational", metrics=metrics,
        )
    except Exception as exc:  # noqa: BLE001
        return ComponentHealth(
            name="quant_intelligence", state=HealthState.CRITICAL,
            message=f"process() failed: {type(exc).__name__}: {exc}",
        )
