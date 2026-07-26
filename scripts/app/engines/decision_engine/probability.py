"""
app/engines/decision_engine/probability.py -- Probability Engine.
------------------------------------------------------------------------------
Estimates Win/Loss/Trade-Success probability. Reuses whatever
probability the upstream engine already computed
(trade_score.py's PROBABILITY_BANDS-derived probability_pct, or Early
Breakout's breakout_score.compute_probability_pct()) as the primary
signal -- rescored here, not recomputed, per the "avoid duplicate
calculations" performance requirement. Blends in overall confidence
and trade quality as secondary adjustments since neither upstream
engine sees those.

Historical Similarity (Replay-based) is only produced when a Replay
engine is registered and attaches a similarity score to the trade --
no Replay engine exists in this codebase yet, so this stays None
until one is added (see adapters.py's module docstring).
"""


def estimate_probability(trade: dict, confidence_score: float) -> dict:
    base_probability = trade.get("probability_pct")
    if base_probability is None:
        # No upstream probability estimate on this trade at all -- fall
        # back to confidence alone, heavily discounted for the missing
        # signal (this should be rare: both trade_score.py and
        # breakout_score.py always set probability_pct today).
        base_probability = confidence_score * 0.6

    win_probability = 0.7 * base_probability + 0.3 * confidence_score
    win_probability = max(0.0, min(100.0, win_probability))
    loss_probability = round(100.0 - win_probability, 1)

    historical_similarity = trade.get("replay_similarity_pct")  # None until a Replay engine exists

    return {
        "win_probability_pct": round(win_probability, 1),
        "loss_probability_pct": loss_probability,
        "trade_success_probability_pct": round(win_probability, 1),
        "historical_similarity_pct": historical_similarity,
        "source": "upstream_probability_pct" if trade.get("probability_pct") is not None else "confidence_fallback",
    }
