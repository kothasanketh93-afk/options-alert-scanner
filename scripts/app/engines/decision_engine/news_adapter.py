"""
app/engines/decision_engine/news_adapter.py -- Part 11.5 integration glue.
------------------------------------------------------------------------------
Wires the independent News Intelligence Engine
(app/engines/news_intelligence/) into the existing, unmodified Decision
Engine as an additional final step -- called from engine.py's evaluate(),
AFTER Quant Intelligence (quant_adapter.attach()) has already run, per the
merge spec: "After Quant Intelligence completes, obtain News Score /
Sentiment / Impact / Confidence / Reasons using NewsDecisionAdapter, merge
into the final decision."

Exact same rollout posture as quant_adapter.py (same file this module was
modeled on):

  * FAIL-SOFT BY DESIGN -- attach() never raises. Any failure (config flag
    off, the news_intelligence package failing to import, or
    NewsDecisionAdapter.get_news_score() itself raising) degrades to
    decision_object["news_intelligence"] = {"enabled": False, ...} and
    leaves every other field of the already-computed Decision Object --
    including whatever quant_adapter.attach() already added -- completely
    untouched. Scanner must never stop because of this module.

  * INFLUENCE IS OPT-IN -- by default (config.NEWS_INTELLIGENCE_INFLUENCE_
    ENABLED = False) News Intelligence is computed and attached for
    visibility/logging only. Flipping the flag on lets a confidently
    negative news read flip an otherwise-approved trade to rejected (see
    `_apply_influence()`) -- it can only make an approved trade MORE
    cautious, mirroring quant_adapter._apply_influence()'s exact rule
    (never overrides an already-rejected trade back to approved, never
    touches trade_quality_score/confidence_score/risk numbers).

  * DISCOVERY, NOT DUPLICATION -- this module does not recompute
    sentiment/impact/mapping itself; it only calls the existing
    NewsDecisionAdapter.get_news_score(symbol) exposed by
    news_intelligence/adapters/decision_adapter.py (already built for
    exactly this call shape, per that module's own docstring: "the plug;
    the socket ... does not exist until someone wires it in a separate
    change" -- this file is that separate change). Trend/EMA/VWAP/ATR/
    RSI/PCR/OI/Volume/IV are never recomputed here either -- News
    Intelligence's own scoring does not need them; only `trade["symbol"]`
    is passed through.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

from logging_setup import get_logger
from config import (
    NEWS_INTELLIGENCE_ENABLED,
    NEWS_INTELLIGENCE_INFLUENCE_ENABLED,
    NEWS_INTELLIGENCE_MIN_SCORE,
)

_logger = get_logger("decision_engine.news_intelligence")

# Resolved lazily (not at import time) so a broken/missing
# news_intelligence package never prevents decision_engine itself from
# importing -- identical posture to quant_adapter._get_quant_engine().
_NewsDecisionAdapter = None
_default_adapter_fn = None
_import_error: Optional[Exception] = None
_import_attempted = False


def _get_news_adapter():
    """Imports app.engines.news_intelligence exactly once. Returns
    (NewsDecisionAdapter class, default_adapter callable) or (None, None)
    if the package is missing or broken -- never raises."""
    global _NewsDecisionAdapter, _default_adapter_fn, _import_error, _import_attempted
    if _import_attempted:
        return _NewsDecisionAdapter, _default_adapter_fn
    _import_attempted = True
    try:
        from app.engines.news_intelligence.adapters.decision_adapter import (
            NewsDecisionAdapter, default_adapter,
        )
        _NewsDecisionAdapter, _default_adapter_fn = NewsDecisionAdapter, default_adapter
    except Exception as exc:  # noqa: BLE001 - defensive, see module docstring
        _import_error = exc
        _logger.error(
            f"NEWS_INTELLIGENCE_IMPORT_FAILED: {type(exc).__name__}: {exc}", exc_info=True
        )
    return _NewsDecisionAdapter, _default_adapter_fn


def _disabled_result(reason: str) -> Dict[str, Any]:
    return {"enabled": False, "reason": reason}


def _apply_influence(decision_object: dict, news_result) -> None:
    """Opt-in only (config.NEWS_INTELLIGENCE_INFLUENCE_ENABLED). Mirrors
    quant_adapter._apply_influence()'s exact rule: can only make an
    approved trade MORE cautious, never flips an already-rejected trade
    back to approved, never touches trade_quality_score/confidence_score/
    risk numbers the existing Decision Engine (and Quant Intelligence,
    if it ran first) already computed."""
    if not decision_object.get("approved", True):
        return  # already rejected -- nothing to flip

    direction = (decision_object.get("__trade_direction__") or "").upper()
    news_score = news_result.news_score  # -100..100, signed
    # A CE (bullish) trade wants a non-negative news_score; a PE
    # (bearish) trade wants a non-positive one. Un-signed magnitude
    # against the trade's own direction is what matters here, same
    # "direction-aware" spirit as quant_adapter's momentum proxy.
    aligned_score = news_score if direction == "CE" else -news_score

    if aligned_score >= NEWS_INTELLIGENCE_MIN_SCORE:
        return  # news is neutral-to-favorable for this trade's direction
    if news_result.confidence < 0.3:
        return  # too little confidence in the read to act on it

    decision_object["approved"] = False
    decision_object["decision"] = "Trade Rejected"
    reason = (
        f"News Intelligence rejected (news_score {news_score:.1f} unfavorable for "
        f"{direction or 'trade'} direction, confidence {news_result.confidence:.2f})"
    )
    decision_object.setdefault("rejection_reasons", []).append(reason)
    decision_object.setdefault("warnings", []).append(
        "Decision flipped by News Intelligence (config.NEWS_INTELLIGENCE_INFLUENCE_ENABLED)"
    )


def attach(trade: dict, decision_object: dict, snapshot: Optional[dict] = None) -> None:
    """Mutates decision_object in place, adding
    decision_object['news_intelligence']. Never raises -- see module
    docstring. `snapshot` accepted for call-signature symmetry with
    quant_adapter.attach() but not currently used -- News Intelligence
    scores off the symbol alone, not price/regime data."""
    if not NEWS_INTELLIGENCE_ENABLED:
        decision_object["news_intelligence"] = _disabled_result(
            "News Intelligence disabled (config.NEWS_INTELLIGENCE_ENABLED = False)"
        )
        return

    _, default_adapter = _get_news_adapter()
    if default_adapter is None:
        decision_object["news_intelligence"] = _disabled_result(
            f"News Intelligence unavailable: "
            f"{type(_import_error).__name__ if _import_error else 'import failed'}"
        )
        return

    symbol = trade.get("symbol")
    if not symbol:
        decision_object["news_intelligence"] = _disabled_result(
            "News Intelligence skipped: trade has no symbol"
        )
        return

    started = time.monotonic()
    try:
        result = default_adapter().get_news_score(symbol)
        elapsed_ms = round((time.monotonic() - started) * 1000, 2)

        decision_object["news_intelligence"] = {
            "enabled": True,
            "news_score": result.news_score,
            "sentiment": result.sentiment,
            "impact": result.impact,
            "confidence": result.confidence,
            "reasons": result.reasons,
            "article_count": result.article_count,
            "most_recent_headline": result.most_recent_headline,
            "most_recent_url": result.most_recent_url,
            "execution_time_ms": elapsed_ms,
        }

        if NEWS_INTELLIGENCE_INFLUENCE_ENABLED:
            # Direction is read off the trade dict, not decision_object,
            # so influence works whether or not quant_adapter ran first.
            decision_object["__trade_direction__"] = trade.get("direction")
            _apply_influence(decision_object, result)
            decision_object.pop("__trade_direction__", None)

        _logger.info(
            f"NEWS_INTELLIGENCE {trade.get('symbol')} {trade.get('direction')}: "
            f"score={result.news_score} sentiment={result.sentiment} impact={result.impact} "
            f"confidence={result.confidence} articles={result.article_count} ({elapsed_ms}ms)"
        )

    except Exception as exc:  # noqa: BLE001 - see module docstring: never interrupt the Decision Engine
        _logger.error(
            f"NEWS_INTELLIGENCE_FAILED {trade.get('symbol')} {trade.get('direction')}: "
            f"{type(exc).__name__}: {exc}",
            exc_info=True,
        )
        decision_object["news_intelligence"] = {
            "enabled": False,
            "error": True,
            "reason": f"News Intelligence internal error, ignored: {exc}",
        }


def health():
    """Health-check callable for production.health_registry -- same
    shape/convention as quant_adapter.health(), registered at startup in
    app/backend/api/app.py."""
    from production.health_states import ComponentHealth, HealthState

    if not NEWS_INTELLIGENCE_ENABLED:
        return ComponentHealth(
            name="news_intelligence", state=HealthState.OFFLINE,
            message="disabled via config.NEWS_INTELLIGENCE_ENABLED",
        )

    _, default_adapter = _get_news_adapter()
    if default_adapter is None:
        return ComponentHealth(
            name="news_intelligence", state=HealthState.CRITICAL,
            message=f"package import failed: {_import_error}",
        )

    try:
        probe = default_adapter().get_news_score("_HEALTHCHECK_")
        metrics = {"article_count": probe.article_count}

        # Fix (production-readiness review): without pydantic installed,
        # news_intelligence/config/loader.py silently falls back to an
        # unvalidated dict-backed settings object (own module docstring,
        # loader.py lines ~9-13) -- a real gap (malformed settings.yaml
        # values would no longer be caught at startup) that was
        # previously visible only as a one-line log warning, easy to
        # miss. Surfacing it as DEGRADED here (checked purely via
        # importlib.util.find_spec -- does NOT import or alter anything
        # inside news_intelligence itself) makes it visible in
        # production.health_registry / the dashboard instead.
        import importlib.util
        if importlib.util.find_spec("pydantic") is None:
            return ComponentHealth(
                name="news_intelligence", state=HealthState.DEGRADED,
                message=(
                    "operational, but pydantic is not installed -- settings.yaml is "
                    "loaded unvalidated (see docs/NEWS_INTELLIGENCE_INTEGRATION.md "
                    "'Manual steps required')"
                ),
                metrics=metrics,
            )

        return ComponentHealth(
            name="news_intelligence", state=HealthState.HEALTHY,
            message="operational", metrics=metrics,
        )
    except Exception as exc:  # noqa: BLE001
        return ComponentHealth(
            name="news_intelligence", state=HealthState.CRITICAL,
            message=f"get_news_score() failed: {type(exc).__name__}: {exc}",
        )
