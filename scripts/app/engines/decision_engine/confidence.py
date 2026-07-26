"""
app/engines/decision_engine/confidence.py -- Confidence Engine.
------------------------------------------------------------------------------
Blends whichever plugins are available this cycle into one 0-100
Overall Confidence Score. Weights are config-driven
(config.DECISION_ENGINE_PLUGIN_WEIGHTS) and automatically renormalized
over only the available plugins, so a missing optional engine never
drags the score down -- it's simply excluded from the average rather
than counted as a zero.
"""

from config import DECISION_ENGINE_PLUGIN_WEIGHTS


def _weight_for(plugin) -> float:
    return DECISION_ENGINE_PLUGIN_WEIGHTS.get(plugin.name, plugin.default_weight)


def overall_confidence(trade: dict, plugins: list) -> dict:
    if not plugins:
        return {"score": 0.0, "breakdown": {}, "engines_used": []}

    weighted_sum = 0.0
    total_weight = 0.0
    breakdown = {}
    for plugin in plugins:
        w = max(0.0, _weight_for(plugin))
        try:
            c = max(0.0, min(100.0, float(plugin.calculate_confidence(trade))))
        except Exception:
            c = 0.0
        breakdown[plugin.name] = round(c, 1)
        weighted_sum += w * c
        total_weight += w

    score = (weighted_sum / total_weight) if total_weight > 0 else 0.0
    return {
        "score": round(max(0.0, min(100.0, score)), 1),
        "breakdown": breakdown,
        "engines_used": [p.name for p in plugins],
    }
