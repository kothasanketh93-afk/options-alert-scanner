"""
app/engines/replay_analytics/learning_engine.py -- Learning Engine.
------------------------------------------------------------------------------
Purely STATISTICAL learning -- group-by + win-rate/expectancy ranking.
Deliberately NOT machine learning (no model fitting, no gradient
descent, no embeddings): per the Part 7 spec, this must stay
transparent and auditable -- every "best timeframe" or "worst market
condition" conclusion here is just performance_analytics.compute_performance()
re-run on a filtered subset, so anyone can re-derive the same number
by hand from the trade journal.

A group is only surfaced once it has >= config.REPLAY_LEARNING_MIN_SAMPLE_SIZE
trades, to avoid "best entry type: 1 trade, 100% win rate" noise.
"""

from collections import defaultdict

from config import REPLAY_LEARNING_MIN_SAMPLE_SIZE
from . import performance_analytics


def _group_by(records: list, key_fn) -> dict:
    groups = defaultdict(list)
    for r in records:
        key = key_fn(r)
        if key is not None:
            groups[key].append(r)
    return groups


def _rank_groups(groups: dict) -> list:
    """Returns [{group, sample_size, win_rate, expectancy_pct, ...}],
    sorted best-expectancy-first, filtered to groups meeting the
    minimum sample size."""
    ranked = []
    for key, recs in groups.items():
        if len(recs) < REPLAY_LEARNING_MIN_SAMPLE_SIZE:
            continue
        perf = performance_analytics.compute_performance(recs)
        ranked.append({"group": key, "sample_size": len(recs), **perf})
    ranked.sort(key=lambda g: g["expectancy_pct"], reverse=True)
    return ranked


def learn_by_market_condition(records: list) -> list:
    return _rank_groups(_group_by(records, lambda r: r.get("market_regime")))


def learn_by_timeframe(records: list) -> list:
    return _rank_groups(_group_by(records, lambda r: r.get("timeframe")))


def learn_by_entry_type(records: list) -> list:
    return _rank_groups(_group_by(records, lambda r: r.get("entry_type") or r.get("strategy")))


def learn_by_breakout_pattern(records: list) -> list:
    return _rank_groups(_group_by(records, lambda r: r.get("pattern")))


def learn_by_option_characteristic(records: list) -> list:
    """Groups by moneyness (ITM/ATM/OTM) -- the one option characteristic
    every trade record already carries; callers with richer per-trade
    Greeks data can pass a records list pre-tagged with any other
    characteristic under the same 'group_key' field and call
    _rank_groups(_group_by(records, lambda r: r.get('group_key')))
    directly for a custom cut."""
    return _rank_groups(_group_by(records, lambda r: r.get("moneyness")))


def generate_learning_report(records: list) -> dict:
    """
    Returns best/worst for each dimension (None if too few groups
    qualified). "Best"/"worst" = highest/lowest expectancy among
    groups that met REPLAY_LEARNING_MIN_SAMPLE_SIZE.
    """
    def _best_worst(ranked):
        if not ranked:
            return {"best": None, "worst": None}
        return {"best": ranked[0], "worst": ranked[-1]}

    return {
        "market_condition": _best_worst(learn_by_market_condition(records)),
        "timeframe": _best_worst(learn_by_timeframe(records)),
        "entry_type": _best_worst(learn_by_entry_type(records)),
        "breakout_pattern": _best_worst(learn_by_breakout_pattern(records)),
        "option_characteristic": _best_worst(learn_by_option_characteristic(records)),
    }
