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
)
from option_chain import days_to_expiry


def oi_is_building(oi_history: dict, instrument_key: str, current_oi: float) -> bool:
    previous = oi_history.get(instrument_key)
    oi_history[instrument_key] = current_oi
    if previous is None or previous <= 0:
        return False
    return (current_oi - previous) / previous >= OI_BUILD_THRESHOLD_PCT


def liquidity_ok(oi: float, volume: float, bid: float, ask: float, premium: float) -> bool:
    if oi < MIN_OI:
        return False
    if volume < MIN_OPTION_VOLUME:
        return False
    if premium < MIN_PREMIUM:
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
    required = (
        "ema_aligned", "vwap_confirmed", "volume_confirmed", "oi_building",
        "liquidity_ok", "mtf_confirmed", "risk_reward_ok",
        "delta_ok", "expiry_theta_ok",
    )
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
