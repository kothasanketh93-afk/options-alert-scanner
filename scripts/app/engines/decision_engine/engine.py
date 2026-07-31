"""
app/engines/decision_engine/engine.py -- Part 6 entry point.
------------------------------------------------------------------------------
evaluate() is the ONE function main.py needs to call, after a trade
dict has already been built by either pipeline:

    Market Snapshot -> Early Breakout -> Options Intelligence -> [HERE] -> Alert
                     (or: Market Snapshot -> Trade Score Engine -> Options Intelligence -> [HERE] -> Alert)

It never re-fetches data or re-runs upstream scoring -- every plugin
in adapters.py reads fields the earlier engines already attached to
the trade dict this cycle (see base.py's module docstring).

    from app.engines.decision_engine import engine as decision_engine

    decision = decision_engine.evaluate(trade, snapshot=snapshot)
    trade["decision"] = decision   # additive field, same pattern as
                                    # trade["options_intelligence"]
    if config.DECISION_ENGINE_FILTER_ENABLED and decision["decision"] == "Trade Rejected":
        continue  # skip alert, same style as the OPTIONS_INTELLIGENCE_FILTER_ENABLED gate

Nothing above changes existing behavior unless
config.DECISION_ENGINE_FILTER_ENABLED is explicitly flipped on (default
False) -- the Decision Object is always computed and attached for
visibility/logging, matching the Options Intelligence rollout pattern.

ENGINE DISCOVERY: build_registry() is the ONE place that lists which
plugins exist. Adding a future engine (Institutional Intelligence,
Trading Intelligence, Replay, AI Prediction) means adding one adapter
class in adapters.py and one line here -- nothing else in this package
changes. Unavailable/optional engines are discovered and skipped per
trade by PluginRegistry.discover(), never by editing this file.
"""

import time
from datetime import datetime, timezone, timedelta

from logging_setup import get_logger
from config import (
    DECISION_ENGINE_ENABLED, DECISION_ENGINE_MIN_CONFIDENCE,
    DECISION_ENGINE_MIN_PROBABILITY, DECISION_ENGINE_MIN_TRADE_QUALITY,
)

from .base import PluginRegistry
from .adapters import build_default_plugins
from . import confidence as confidence_mod
from . import probability as probability_mod
from . import risk as risk_mod
from . import entry_validation as entry_validation_mod
from . import stop_loss as stop_loss_mod
from . import target as target_mod
from . import trade_quality as trade_quality_mod
from . import explanation as explanation_mod
from . import quant_adapter
from . import news_adapter
from . import candlestick_adapter

_logger = get_logger("decision_engine")
IST = timezone(timedelta(hours=5, minutes=30))


def build_registry() -> PluginRegistry:
    """The ONE place future engines get registered. See module
    docstring above."""
    registry = PluginRegistry()
    for plugin in build_default_plugins():
        registry.register(plugin)
    # Future: registry.register(InstitutionalIntelligencePlugin())
    # Future: registry.register(TradingIntelligencePlugin())
    # Future: registry.register(ReplayPlugin())
    # Future: registry.register(AIPredictionPlugin())
    return registry


def _passthrough_object(reason: str) -> dict:
    """Used when the Decision Engine is disabled entirely
    (DECISION_ENGINE_ENABLED = False) -- fully restores pre-Part-6
    behavior, same "master flag" pattern as OPTIONS_INTELLIGENCE_ENABLED."""
    return {
        "enabled": False,
        "decision": "Trade Approved",
        "reason": reason,
        "timestamp": datetime.now(IST).isoformat(),
    }


def evaluate(trade: dict, snapshot: dict = None) -> dict:
    """
    Returns the Standard Decision Object. Never raises -- any internal
    failure degrades to an approve-with-warning rather than blocking an
    otherwise-valid alert pipeline (the Decision Engine is meant to add
    a final layer of judgment, not become a new single point of failure
    for the whole scanner).
    """
    started = time.monotonic()

    if not DECISION_ENGINE_ENABLED:
        return _passthrough_object("Decision Engine disabled (config.DECISION_ENGINE_ENABLED = False)")

    try:
        registry = build_registry()
        context = {"trade": trade, "snapshot": snapshot}
        plugins = registry.discover(trade, context)
        plugins_by_name = {p.name: p for p in plugins}

        confidence = confidence_mod.overall_confidence(trade, plugins)
        probability = probability_mod.estimate_probability(trade, confidence["score"])
        risk = risk_mod.calculate_risk(trade)
        risk_reward = risk_mod.calculate_risk_reward(trade)
        position_size = risk_mod.calculate_position_size(trade, risk)
        entry_validation = entry_validation_mod.validate_entry(trade, plugins_by_name)
        stop_loss = stop_loss_mod.evaluate_stop_loss(trade)
        targets = target_mod.calculate_targets(trade, probability)
        trade_quality = trade_quality_mod.calculate_trade_quality(
            confidence, probability, risk, risk_reward, entry_validation,
        )

        rejection_reasons = explanation_mod.collect_rejection_reasons(
            plugins, trade, risk_reward, entry_validation, trade_quality,
            confidence, probability,
            DECISION_ENGINE_MIN_CONFIDENCE, DECISION_ENGINE_MIN_PROBABILITY,
            DECISION_ENGINE_MIN_TRADE_QUALITY,
        )
        approved = len(rejection_reasons) == 0
        priority = trade_quality_mod.assign_priority(
            trade_quality["trade_quality_score"], confidence["score"],
            probability["win_probability_pct"], approved,
        )
        explanation = explanation_mod.build_explanation(
            approved, plugins, trade, trade_quality, probability, risk_reward, rejection_reasons,
        )

        warnings = []
        if not plugins:
            warnings.append("No intelligence engines were available for this trade")
        if risk.get("warning"):
            warnings.append(risk["warning"])

        elapsed_ms = round((time.monotonic() - started) * 1000, 2)

        decision_object = {
            "enabled": True,
            "decision": explanation["decision"],
            "approved": approved,
            "trade_quality_score": trade_quality["trade_quality_score"],
            "confidence_score": confidence["score"],
            "probability": probability,
            "risk": risk,
            "risk_reward": risk_reward,
            "entry": risk_reward["entry"],
            "stop_loss": stop_loss["active_stop_loss"],
            "stop_loss_detail": stop_loss,
            "target_1": targets["target_1"],
            "target_2": targets["target_2"],
            "target_3": targets["target_3"],
            "position_size": position_size,
            "entry_validation": entry_validation,
            "priority": priority,
            "explanation": explanation["reasons"],
            "rejection_reasons": rejection_reasons,
            "warnings": warnings,
            "engines_used": registry.all_names(),
            "engines_available": [p.name for p in plugins],
            "execution_time_ms": elapsed_ms,
            "timestamp": datetime.now(IST).isoformat(),
        }

        _logger.info(
            f"DECISION {trade.get('symbol')} {trade.get('direction')}: {decision_object['decision']} | "
            f"Quality={decision_object['trade_quality_score']} Confidence={decision_object['confidence_score']} "
            f"Probability={probability['win_probability_pct']}% RR={risk_reward['risk_reward_ratio']} "
            f"Priority={priority} Engines={decision_object['engines_available']} "
            f"({elapsed_ms}ms)"
        )
        if not approved:
            _logger.info(f"DECISION_REJECTED {trade.get('symbol')} {trade.get('direction')}: {rejection_reasons}")

        # Part 11 -- Quant Intelligence Engine: invoked LAST, after every
        # existing decision has already been made. Mutates decision_object
        # in place, adding decision_object["quant_intelligence"]; never
        # changes approved/priority/risk/etc. unless
        # config.QUANT_INTELLIGENCE_INFLUENCE_ENABLED is explicitly turned
        # on (default off). quant_adapter.attach() already never raises,
        # but it's wrapped here too so a hypothetical bug in that optional
        # add-on can never cost us the Decision Object this function just
        # spent all that work computing -- see quant_adapter.py's
        # module docstring.
        try:
            quant_adapter.attach(trade, decision_object, snapshot)
        except Exception as qi_exc:  # noqa: BLE001
            _logger.error(
                f"QUANT_INTELLIGENCE_ATTACH_FAILED {trade.get('symbol')} {trade.get('direction')}: "
                f"{type(qi_exc).__name__}: {qi_exc}", exc_info=True,
            )
            decision_object["quant_intelligence"] = {"enabled": False, "error": True, "reason": str(qi_exc)}

        # Part 11.5 -- News Intelligence Engine: invoked after Quant
        # Intelligence, per the merge spec. Mutates decision_object in
        # place, adding decision_object["news_intelligence"]; never
        # changes approved/priority unless
        # config.NEWS_INTELLIGENCE_INFLUENCE_ENABLED is explicitly turned
        # on (default off). news_adapter.attach() already never raises,
        # but it's wrapped here too for the same reason quant_adapter's
        # call is -- see news_adapter.py's module docstring.
        try:
            news_adapter.attach(trade, decision_object, snapshot)
        except Exception as ni_exc:  # noqa: BLE001
            _logger.error(
                f"NEWS_INTELLIGENCE_ATTACH_FAILED {trade.get('symbol')} {trade.get('direction')}: "
                f"{type(ni_exc).__name__}: {ni_exc}", exc_info=True,
            )
            decision_object["news_intelligence"] = {"enabled": False, "error": True, "reason": str(ni_exc)}

        # Candlestick Pattern Recognition: invoked last. Mutates
        # decision_object in place, adding
        # decision_object["candlestick_patterns"]; only nudges
        # confidence_score (never approved/decision) and only when
        # config.CANDLESTICK_PATTERNS_INFLUENCE_ENABLED is explicitly
        # turned on (default off). candlestick_adapter.attach() already
        # never raises, but it's wrapped here too for the same reason
        # the two calls above are -- see candlestick_adapter.py's module
        # docstring.
        try:
            candlestick_adapter.attach(trade, decision_object)
        except Exception as cs_exc:  # noqa: BLE001
            _logger.error(
                f"CANDLESTICK_PATTERNS_ATTACH_FAILED {trade.get('symbol')} {trade.get('direction')}: "
                f"{type(cs_exc).__name__}: {cs_exc}", exc_info=True,
            )
            decision_object["candlestick_patterns"] = {"enabled": False, "error": True, "reason": str(cs_exc)}

        return decision_object

    except Exception as exc:
        _logger.error(
            f"DECISION_ENGINE_FAILED {trade.get('symbol')} {trade.get('direction')}: "
            f"{type(exc).__name__}: {exc}", exc_info=True,
        )
        obj = _passthrough_object(f"Decision Engine internal error, approved by default: {exc}")
        obj["error"] = True
        return obj
