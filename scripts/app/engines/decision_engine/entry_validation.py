"""
app/engines/decision_engine/entry_validation.py -- Entry Validation Engine.
------------------------------------------------------------------------------
Scores entry quality from signals already on the trade dict (breakout
distance from Early Breakout's entry_zone/maximum_entry, volume/
liquidity confirmation from trade_score.py's factor_checklist, options
confirmation from the Options Intelligence plugin, institutional
confirmation from Early Breakout's institutional_score when present).
"""


def validate_entry(trade: dict, plugins_by_name: dict) -> dict:
    reasons = []
    sub_scores = {}

    # Breakout / entry distance (Early Breakout only -- maximum_entry is
    # its hard "never chase" ceiling, already enforced upstream; here we
    # just score how close to ideal the current premium sits).
    if "maximum_entry" in trade and trade.get("alert_premium") is not None:
        premium = trade["alert_premium"]
        max_entry = trade["maximum_entry"]
        breakout_price = trade.get("breakout_price") or premium
        if max_entry > breakout_price:
            distance_ratio = (premium - breakout_price) / (max_entry - breakout_price)
            sub_scores["entry_distance"] = max(0.0, min(100.0, 100.0 - distance_ratio * 100.0))
        else:
            sub_scores["entry_distance"] = 50.0
    else:
        sub_scores["entry_distance"] = 60.0  # neutral -- no distance signal available (plain scanner path)

    # Volume confirmation -- read from whichever engine already judged it.
    liquidity_plugin = plugins_by_name.get("options_intelligence")
    if liquidity_plugin is not None:
        oi = trade.get("options_intelligence", {})
        sub_scores["volume_confirmation"] = float(oi.get("liquidity_score", 50))
    else:
        sub_scores["volume_confirmation"] = 50.0

    # Market alignment -- from Market Snapshot plugin if present.
    market_plugin = plugins_by_name.get("market_snapshot")
    sub_scores["market_alignment"] = market_plugin.calculate_score(trade) if market_plugin else 50.0

    # Options confirmation.
    oi_plugin = plugins_by_name.get("options_intelligence")
    sub_scores["options_confirmation"] = oi_plugin.calculate_score(trade) if oi_plugin else 50.0

    # Institutional confirmation -- only present via Early Breakout today.
    if "institutional_score" in trade:
        sub_scores["institutional_confirmation"] = float(trade["institutional_score"])
    else:
        sub_scores["institutional_confirmation"] = None  # genuinely unavailable, not scored as zero

    scored = [v for v in sub_scores.values() if v is not None]
    entry_quality_score = round(sum(scored) / len(scored), 1) if scored else 0.0

    if sub_scores["entry_distance"] < 20:
        reasons.append("Entry too far beyond breakout/ideal zone")
    if sub_scores["volume_confirmation"] < 30:
        reasons.append("Weak volume/liquidity confirmation")

    return {
        "entry_quality_score": entry_quality_score,
        "sub_scores": sub_scores,
        "passed": entry_quality_score >= 40 and not reasons,
        "reasons": reasons,
    }
