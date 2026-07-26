"""
liquidity_premium.py -- Option Liquidity Engine, Premium Analysis Engine.
------------------------------------------------------------------------------
LIQUIDITY: deliberately NOT reimplemented here. filters.liquidity_ok()
is the existing hard gate and trade_score.liquidity_strength() is the
existing continuous 0-1 read (OI/volume/spread, index-vs-stock
threshold aware) -- this module's liquidity_score() is a thin
pass-through to the latter, per the "do not duplicate calculations
already performed by previous engines" instruction. Nothing about
spread/OI/volume math is recomputed here.

PREMIUM ANALYSIS: this genuinely didn't exist before -- intrinsic vs.
extrinsic (time) value split, which nothing upstream computes.
"""

import trade_score
from config import PREMIUM_TIME_VALUE_MAX_PCT


def liquidity_score(oi: float, volume: float, bid: float, ask: float,
                     premium: float, symbol: str = None) -> float:
    """Pass-through to trade_score.liquidity_strength() -- see module
    docstring. Kept as its own function (rather than calling
    trade_score directly from scoring.py) so every Part-5 sub-score is
    reachable from one place (this package) with a consistent name."""
    return trade_score.liquidity_strength(oi, volume, bid, ask, premium, symbol)


def intrinsic_value(spot_price: float, strike: float, direction: str) -> float:
    if direction == "CE":
        return max(0.0, spot_price - strike)
    return max(0.0, strike - spot_price)


def extrinsic_value(premium: float, intrinsic: float) -> float:
    """AKA time value. Floored at 0 -- a premium quoted below intrinsic
    is a stale/crossed quote, not negative time value."""
    return max(0.0, premium - intrinsic)


def time_value_pct(premium: float, extrinsic: float) -> float:
    if not premium:
        return 1.0  # a zero/garbage premium is effectively all "risk", not a genuine bargain
    return extrinsic / premium


def premium_score(spot_price: float, strike: float, direction: str, premium: float) -> float:
    """
    0-1: penalizes premiums that are ALMOST ENTIRELY time value (deep
    OTM "lottery ticket" pricing, per PREMIUM_TIME_VALUE_MAX_PCT) --
    every rupee here is theta-exposed with nothing already "in the
    bank" as intrinsic value. An ITM strike with real intrinsic value
    scores highest; a strike right at the configured time-value
    ceiling scores near zero.
    """
    if not premium:
        return 0.0
    intrinsic = intrinsic_value(spot_price, strike, direction)
    extrinsic = extrinsic_value(premium, intrinsic)
    pct_time_value = time_value_pct(premium, extrinsic)
    if PREMIUM_TIME_VALUE_MAX_PCT <= 0:
        return 0.5
    return max(0.0, min(1.0, 1.0 - (pct_time_value / PREMIUM_TIME_VALUE_MAX_PCT)))


def premium_breakdown(spot_price: float, strike: float, direction: str, premium: float) -> dict:
    """Convenience bundle for the Standard Output Object / logging --
    every value premium_score() derives internally, surfaced for
    display rather than recomputed by the caller."""
    intrinsic = intrinsic_value(spot_price, strike, direction)
    extrinsic = extrinsic_value(premium, intrinsic)
    return {
        "intrinsic_value": round(intrinsic, 2),
        "extrinsic_value": round(extrinsic, 2),
        "time_value_pct": round(time_value_pct(premium, extrinsic), 3) if premium else None,
    }
