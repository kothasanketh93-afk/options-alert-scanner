"""
levels.py -- Dynamic ATR-based trade levels, split out of scanner.py.
------------------------------------------------------------------------
SL/target/entry-zone sizing, capped against nearby support/resistance,
plus the expected-holding-time estimate derived from the same ATR math.
"""

from config import (
    SL_ATR_MULT, T1_ATR_MULT, T2_ATR_MULT,
    ENTRY_ZONE_ATR_FRACTION, MIN_ENTRY_ZONE_PCT, MAX_ENTRY_ZONE_PCT,
    MIN_LEVEL_DISTANCE_ATR_MULT,
)


def dynamic_trade_levels(premium: float, atr_index_points: float, delta: float,
                          direction: str, spot_price: float, nearby_level) -> dict:
    """
    Raises ValueError if atr_index_points or delta is non-positive --
    this represents bad/missing input data, and silently proceeding
    would produce SL/T1/T2 all equal to the entry premium (premium_atr
    would be 0), which the tracker would then read as an instant false
    SL or T1 hit on the very next candle. Fail loudly here instead so
    a bad data pull surfaces immediately rather than as a corrupted
    trade downstream.
    """
    if atr_index_points is None or atr_index_points <= 0:
        raise ValueError(f"dynamic_trade_levels: atr_index_points must be > 0, got {atr_index_points}")
    if delta is None or delta == 0:
        raise ValueError(f"dynamic_trade_levels: delta must be non-zero, got {delta}")

    delta = abs(delta)
    premium_atr = atr_index_points * delta

    sl = premium - premium_atr * SL_ATR_MULT
    t1 = premium + premium_atr * T1_ATR_MULT
    t2 = premium + premium_atr * T2_ATR_MULT

    if nearby_level is not None:
        index_distance_to_level = abs(nearby_level - spot_price)
        # BUGFIX (2026-07-24, live pattern): confirmed live, targets were
        # being hit almost immediately (e.g. T2 hit in 8 minutes with
        # price still running +2% past it) far more often than the raw
        # ATR-based T2 alone would explain. Root cause: find_nearby_level()
        # looks for the recent high/low over its own lookback window --
        # but right at a FRESH breakout, the breakout candle itself is
        # often what sets that recent high/low, making index_distance_to_level
        # come out near zero. Capping T2 at 90% of a near-zero distance
        # clamps the target down to almost nothing, even though there's
        # no real resistance/support actually sitting there -- it's just
        # where price already is. Only apply the cap when the level is at
        # least MIN_LEVEL_DISTANCE_ATR_MULT ATR away, i.e. only when it
        # represents a genuine level AHEAD of price, not the price itself.
        if index_distance_to_level >= atr_index_points * MIN_LEVEL_DISTANCE_ATR_MULT:
            premium_distance_to_level = index_distance_to_level * delta
            capped_t2 = premium + premium_distance_to_level * 0.9
            t2 = min(t2, capped_t2)
            t1 = min(t1, t2 * 0.7 + premium * 0.3)

    sl = max(sl, premium * 0.5)  # floor: never risk more than 50% of premium

    # Entry zone now scales with volatility too, not a fixed 0.99x/1.02x --
    # floored/capped so it stays fillable on quiet days and sane on wild ones.
    entry_zone_width = premium_atr * ENTRY_ZONE_ATR_FRACTION
    entry_zone_pct = entry_zone_width / premium if premium > 0 else MIN_ENTRY_ZONE_PCT
    entry_zone_pct = max(MIN_ENTRY_ZONE_PCT, min(MAX_ENTRY_ZONE_PCT, entry_zone_pct))
    entry_low = premium * (1 - entry_zone_pct / 2)
    entry_high = premium * (1 + entry_zone_pct / 2)

    return {
        "entry_low": round(entry_low, 2),
        "entry_high": round(entry_high, 2),
        "stop_loss": round(sl, 2),
        "target_1": round(t1, 2),
        "target_2": round(t2, 2),
    }


def estimated_holding_minutes(atr_index_points: float, target_distance_index_points: float) -> tuple:
    if atr_index_points <= 0:
        return (20, 40)
    atr_units = target_distance_index_points / atr_index_points
    low = max(10, round(atr_units * 10))
    high = max(low + 10, round(atr_units * 20))
    return (low, high)
