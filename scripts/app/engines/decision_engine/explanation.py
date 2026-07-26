"""
app/engines/decision_engine/explanation.py -- Trade Explanation Engine +
Trade Rejection Engine.
------------------------------------------------------------------------------
Every trade the Decision Engine touches gets a human-readable
explanation -- approvals get "why", rejections get "why not", both are
stored on the Decision Object so main.py/telegram_notify.py can surface
them without recomputing anything.
"""


def build_explanation(approved: bool, plugins: list, trade: dict,
                       trade_quality: dict, probability: dict, risk_reward: dict,
                       rejection_reasons: list) -> dict:
    if approved:
        lines = [
            "Trade Approved",
            f"Trade Quality {trade_quality['trade_quality_score']}/100",
            f"Probability {probability['win_probability_pct']}%",
            f"Risk:Reward 1:{risk_reward['risk_reward_ratio']}",
        ]
        for plugin in plugins:
            try:
                lines.append(plugin.explain(trade))
            except Exception:
                continue
        return {"decision": "Trade Approved", "reasons": lines}

    lines = ["Trade Rejected"] + rejection_reasons
    return {"decision": "Trade Rejected", "reasons": lines}


def collect_rejection_reasons(plugins: list, trade: dict, risk_reward: dict,
                               entry_validation: dict, trade_quality: dict,
                               confidence: dict, probability: dict,
                               min_confidence: float, min_probability: float,
                               min_trade_quality: float) -> list:
    reasons = []

    for plugin in plugins:
        try:
            passed, plugin_reasons = plugin.validate(trade)
        except Exception:
            continue
        if not passed:
            reasons.extend(plugin_reasons or [f"{plugin.name} validation failed"])

    if not risk_reward["passes_rr_floor"]:
        reasons.append(
            f"Poor Risk Reward ({risk_reward['risk_reward_ratio']} below floor {risk_reward['min_rr_threshold']})"
        )
    if not entry_validation["passed"]:
        reasons.extend(entry_validation["reasons"] or ["Poor Entry Quality"])
    if confidence["score"] < min_confidence:
        reasons.append(f"Low Confidence ({confidence['score']} below {min_confidence})")
    if probability["win_probability_pct"] < min_probability:
        reasons.append(f"Low Probability ({probability['win_probability_pct']}% below {min_probability}%)")
    if trade_quality["trade_quality_score"] < min_trade_quality:
        reasons.append(f"Low Trade Quality ({trade_quality['trade_quality_score']} below {min_trade_quality})")

    return reasons
