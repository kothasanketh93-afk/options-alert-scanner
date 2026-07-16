"""
filters.py -- Trade Quality Gate factor checks, split out of scanner.py.
--------------------------------------------------------------------------
Individual pass/fail checks (liquidity, OI build, delta/theta floors)
plus the gate itself: ALL required factors must pass or the setup is
rejected outright -- no partial-credit alert.
"""

from config import (
    MIN_OI, MIN_OPTION_VOLUME, MIN_PREMIUM, MAX_SPREAD_PCT,
    OI_BUILD_THRESHOLD_PCT, MIN_DELTA_OTM,
    EXPIRY_DAY_MIN_OTM_DELTA, EXPIRY_DAY_THRESHOLD_DAYS,
    STOCK_SYMBOLS, STOCK_MIN_OI, STOCK_MIN_OPTION_VOLUME, STOCK_MIN_PREMIUM,
)
from option_chain import days_to_expiry


def oi_is_building(oi_history: dict, instrument_key: str, current_oi: float) -> bool:
    previous = oi_history.get(instrument_key)
    oi_history[instrument_key] = current_oi
    if previous is None or previous <= 0:
        return False
    return (current_oi - previous) / previous >= OI_BUILD_THRESHOLD_PCT


def liquidity_ok(oi: float, volume: float, bid: float, ask: float, premium: float,
                  symbol: str = None) -> bool:
    """
    symbol is optional (defaults to the index thresholds below for
    backward compatibility) -- pass it for stock symbols so
    STOCK_MIN_OI/STOCK_MIN_OPTION_VOLUME/STOCK_MIN_PREMIUM apply
    instead of the NIFTY/BANKNIFTY-tuned globals, which are far too
    strict for mid/smallcap stock options and would reject nearly
    every stock strike outright otherwise.
    """
    is_stock = symbol in STOCK_SYMBOLS
    min_oi = STOCK_MIN_OI if is_stock else MIN_OI
    min_volume = STOCK_MIN_OPTION_VOLUME if is_stock else MIN_OPTION_VOLUME
    min_premium = STOCK_MIN_PREMIUM if is_stock else MIN_PREMIUM

    if oi < min_oi:
        return False
    if volume < min_volume:
        return False
    if premium < min_premium:
        return False
    spread_pct = (ask - bid) / premium
    if spread_pct > MAX_SPREAD_PCT:
        return False
    return True


def delta_acceptable(moneyness: str, delta: float) -> bool:
    """
    Rejects deep-OTM "lottery ticket" strikes: below MIN_DELTA_OTM,
    an OTM option's probability of reaching target is low enough that
    passing every other gate doesn't compensate for it. ATM/ITM aren't
    subject to this floor -- their delta is naturally higher.
    """
    if moneyness != "OTM":
        return True
    return abs(delta) >= MIN_DELTA_OTM


def expiry_theta_ok(moneyness: str, delta: float, expiry_date_str: str, now=None) -> bool:
    """
    Stricter delta floor specifically for OTM strikes within
    EXPIRY_DAY_THRESHOLD_DAYS of expiry, where theta decay accelerates
    sharply and dominates any directional signal. Separate from
    delta_acceptable()/MIN_DELTA_OTM because the risk compounds
    specifically in the last day(s) before expiry -- a delta that's
    fine three days out can be a bad trade the day before expiry.
    """
    if moneyness != "OTM":
        return True
    if days_to_expiry(expiry_date_str, now) > EXPIRY_DAY_THRESHOLD_DAYS:
        return True
    return abs(delta) >= EXPIRY_DAY_MIN_OTM_DELTA


def quality_gate(factors: dict) -> bool:
    """
    HARD RISK GATES ONLY (as of 2026-07-17 rewrite).

    These three are non-negotiable safety checks, not "how good does
    this setup look" signals -- failing any one means the trade is
    structurally unsafe (illiquid, wrong risk profile, or theta-
    exposed) regardless of how strong the directional signals are.

    Previously this required ALL 9 factors (including trend/volume/
    OI/MTF/RR alignment) to be True simultaneously -- an unrealistic
    AND across 6 semi-independent signals that, in 26 consecutive
    live scan cycles, never once passed. The other 6 factors are now
    scored on a weighted 0-100 scale via grading.py's
    example_quality_score()+compute_grade() (already existed in the
    codebase but was previously a no-op downstream of this gate,
    since everything reaching it already had strengths=1.0 across the
    board or was rejected here first). config.ENTRY_SCORE is the
    live alert threshold on that weighted score.
    """
    required = ("liquidity_ok", "delta_ok", "expiry_theta_ok")
    return all(factors.get(k) is True for k in required)


def reasons_from_factors(factors: dict) -> list:
    labels = {
        "ema_aligned": "EMA aligned",
        "vwap_confirmed": "Above VWAP" if factors.get("direction") == "CE" else "Below VWAP",
        "volume_confirmed": "Volume Expansion",
        "oi_building": "Strong OI Build-up",
        "liquidity_ok": "Excellent Liquidity",
        "mtf_confirmed": "15-minute Confirmed",
        "risk_reward_ok": "No Nearby Resistance" if factors.get("direction") == "CE" else "No Nearby Support",
        "delta_ok": "Healthy Delta",
        "expiry_theta_ok": "Theta Risk Acceptable",
    }
    return [f"✓ {labels[k]}" for k in labels if factors.get(k) is True]
