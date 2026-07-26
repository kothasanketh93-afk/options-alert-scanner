"""
app/engines/decision_engine/target.py -- Target Engine.
------------------------------------------------------------------------------
Target 1 / Target 2 already exist on the trade dict (levels.py or
Early Breakout's own ATR sizing). This engine adds an OPTIONAL,
additive Target 3 -- a probability-weighted extension beyond T2 --
without touching T1/T2, which the tracker (tracker.py) already reads
and manages hit/miss state for.
"""


def calculate_targets(trade: dict, probability: dict) -> dict:
    entry = trade.get("alert_premium")
    target_1 = trade.get("target_1")
    target_2 = trade.get("target_2")

    target_3 = None
    if entry is not None and target_2 is not None:
        win_prob = (probability.get("win_probability_pct") or 50) / 100.0
        # Higher win probability -> proportionally more room extrapolated
        # past T2, capped at a further 50% of the T1->T2 leg to stay
        # conservative (this is advisory only -- tracker.py never reads
        # target_3, so it can't affect trade management).
        t1_t2_leg = max(0.0, target_2 - (target_1 if target_1 is not None else entry))
        target_3 = round(target_2 + t1_t2_leg * min(0.5, win_prob), 2)

    return {
        "target_1": target_1,
        "target_2": target_2,
        "target_3": target_3,
        "target_3_note": "Advisory only -- not tracked by tracker.py" if target_3 is not None else None,
    }
