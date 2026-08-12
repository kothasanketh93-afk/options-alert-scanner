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

    BUGFIX (2026-08-10): for ATM/OTM strikes, intrinsic value is
    ALWAYS exactly 0 by definition -- meaning pct_time_value was
    ALWAYS ~100% (>= PREMIUM_TIME_VALUE_MAX_PCT) and this returned 0.0
    for every single ATM/OTM strike regardless of the actual quote's
    quality. Confirmed live: this is the direct source of the "Poor
    Premium Quality (mostly time value)" warning appearing on every
    ATM/OTM OPTIONS_INTEL log line in the logs reviewed this session --
    not a quote-quality signal at all, just this strike's moneyness.
    Once config.OPTIONS_INTELLIGENCE_FILTER_ENABLED was turned on (see
    that flag's own history), scoring.options_filter()'s "any hard-
    hygiene sub-score < 0.2 -> reject" rule turned this into an
    unconditional reject of every ATM/OTM strike, leaving only ITM
    strikes able to ever pass -- a moneyness bias never intended: this
    system deliberately scores ITM/ATM/OTM as three parallel,
    independently-competing strategies (see scanner.moneyness_offsets()/
    STRIKE_RANGE), not a de facto ITM-only filter.

    The intrinsic/extrinsic split this function is built on is only a
    meaningful quality signal for ITM strikes -- there, most of the
    premium SHOULD be intrinsic, so a high time-value percentage on an
    ITM strike (e.g. a barely-ITM strike right before expiry with
    skewed IV) genuinely is an anomaly worth penalizing. For ATM/OTM,
    intrinsic == 0 always, so the entire metric is structurally
    inapplicable, not evidence of a bad quote -- returning a neutral,
    non-penalizing 1.0 there. Genuinely bad ATM/OTM premiums are still
    caught elsewhere: filters.premium_in_range() (absolute band per
    symbol) and this same engine's OTHER sub-scores (liquidity, IV,
    greeks) don't share this blind spot.
    """
    if not premium:
        return 0.0
    intrinsic = intrinsic_value(spot_price, strike, direction)
    if intrinsic <= 0:
        return 1.0  # ATM/OTM -- see BUGFIX note above; not a quality read for these
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
