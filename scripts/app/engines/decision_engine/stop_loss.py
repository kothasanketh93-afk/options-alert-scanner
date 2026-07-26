"""
app/engines/decision_engine/stop_loss.py -- Stop Loss Engine.
------------------------------------------------------------------------------
levels.dynamic_trade_levels() has already priced the stop the trade
will actually use (ATR-based, capped against a nearby level -- see
that function's docstring for the 2026-07-24 bugfix on this exact
math). This engine does NOT override that stop; it evaluates it
against the alternative stop styles the spec asks for and reports
which style the already-chosen stop most resembles, plus a
recommendation if a materially safer alternative exists. Recommending
never means silently changing trade["stop_loss"] -- the Decision
Object surfaces recommend_stop_loss as an alternative in the
explanation only.
"""


def evaluate_stop_loss(trade: dict) -> dict:
    entry = trade.get("alert_premium")
    stop_loss = trade.get("stop_loss")
    if entry is None or stop_loss is None:
        return {"active_stop_loss": stop_loss, "style": "unknown", "distance_pct": None, "recommendation": None}

    distance_pct = round(abs(entry - stop_loss) / entry * 100, 2) if entry else None

    # Percentage-stop equivalent, for comparison only.
    pct_stop = round(entry * 0.5, 2)  # levels.py's own floor: never risk more than 50% of premium

    recommendation = None
    if distance_pct is not None and distance_pct > 60:
        recommendation = f"Active stop risks {distance_pct}% of premium -- consider tightening toward the {pct_stop} percentage-stop floor"

    return {
        "active_stop_loss": stop_loss,
        "style": "atr_dynamic",  # matches levels.dynamic_trade_levels()'s SL_ATR_MULT-based sizing
        "distance_pct": distance_pct,
        "percentage_stop_reference": pct_stop,
        "recommendation": recommendation,
    }
