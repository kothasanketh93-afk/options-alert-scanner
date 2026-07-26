"""
app/engines/decision_engine/trade_quality.py -- Trade Quality Engine +
Alert Priority Engine.
------------------------------------------------------------------------------
Trade Quality blends everything the Decision Engine has computed this
cycle into one 0-100 score. Priority is then a simple threshold walk
over Quality/Confidence/Probability/Risk, same "highest bucket first
match wins" style as config.EARLY_BREAKOUT_GRADE_THRESHOLDS /
TRADE_SCORE_GRADE_THRESHOLDS elsewhere in this codebase.
"""

from config import DECISION_ENGINE_PRIORITY_THRESHOLDS


def calculate_trade_quality(confidence: dict, probability: dict, risk: dict,
                             risk_reward: dict, entry_validation: dict) -> dict:
    components = {
        "confidence": confidence["score"],
        "probability": probability["win_probability_pct"],
        "risk": risk.get("risk_score") or 0.0,
        "entry_quality": entry_validation["entry_quality_score"],
        "risk_reward": min(100.0, (risk_reward["risk_reward_ratio"] / max(risk_reward["min_rr_threshold"], 0.01)) * 60.0),
    }
    weights = {
        "confidence": 0.25, "probability": 0.25, "risk": 0.15,
        "entry_quality": 0.20, "risk_reward": 0.15,
    }
    score = sum(components[k] * weights[k] for k in components)
    return {
        "trade_quality_score": round(max(0.0, min(100.0, score)), 1),
        "components": {k: round(v, 1) for k, v in components.items()},
    }


def assign_priority(trade_quality_score: float, confidence_score: float,
                     probability_pct: float, approved: bool) -> str:
    if not approved:
        return "Rejected"
    for threshold, label in DECISION_ENGINE_PRIORITY_THRESHOLDS:
        if trade_quality_score >= threshold:
            return label
    return "Low"
