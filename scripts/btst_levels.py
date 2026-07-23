"""
btst_levels.py -- Overnight SL/target sizing.
-----------------------------------------------
Deliberately NOT built on levels.py's ATR-based dynamic_trade_levels()
-- that engine is calibrated for a 5-60 minute intraday hold, and
sharing it here would mean any future intraday ATR-tuning change
silently reshapes overnight risk too (or vice versa). This is plain
percentage-of-premium arithmetic instead, sized from btst_config.py's
own constants -- see that file for the "why wider than intraday"
reasoning.
"""

from btst_config import BTST_SL_PCT, BTST_T1_PCT, BTST_T2_PCT, BTST_ENTRY_ZONE_PCT


def overnight_trade_levels(premium: float) -> dict:
    """
    Raises ValueError on non-positive premium -- same "fail loudly on
    bad input" posture as levels.dynamic_trade_levels(), so a bad
    EOD quote surfaces immediately instead of producing a zero-width
    or negative entry zone downstream.
    """
    if premium is None or premium <= 0:
        raise ValueError(f"overnight_trade_levels: premium must be > 0, got {premium}")

    sl = premium * (1 - BTST_SL_PCT)
    t1 = premium * (1 + BTST_T1_PCT)
    t2 = premium * (1 + BTST_T2_PCT)
    entry_low = premium * (1 - BTST_ENTRY_ZONE_PCT / 2)
    entry_high = premium * (1 + BTST_ENTRY_ZONE_PCT / 2)

    return {
        "entry_low": round(entry_low, 2),
        "entry_high": round(entry_high, 2),
        "stop_loss": round(sl, 2),
        "target_1": round(t1, 2),
        "target_2": round(t2, 2),
    }
