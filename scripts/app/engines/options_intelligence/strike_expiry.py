"""
strike_expiry.py -- Strike Selection Engine, Expiry Analysis Engine.
------------------------------------------------------------------------------
STRIKE SELECTION: scanner.py already resolves ITM/ATM/OTM buckets
(moneyness_offsets()/resolve_strike_candidates()) and scores every
strike in each bucket -- this module does NOT re-select a strike. It
classifies how far in/out of the money the ALREADY-CHOSEN strike is
(plain "ITM"/"OTM" -> finer "Deep ITM"/"ITM"/"ATM"/"OTM"/"Deep OTM"),
which is new, and folds that into the strike_score() to nudge the
Options Intelligence Score toward strikes with a healthier probability
profile (Deep OTM/Deep ITM both score lower -- lottery-ticket vs.
capital-inefficient).

EXPIRY ANALYSIS: option_chain.days_to_expiry()/current_expiry() are
reused as-is (not recomputed) -- this module only adds the
weekly/monthly classification and the decay-proximity score.
"""

from config import EXPIRY_WEEKLY_MAX_DAYS, EXPIRY_DAY_THRESHOLD_DAYS


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


# ---------------------------------------------------------------------------
# Strike Selection Engine
# ---------------------------------------------------------------------------

def strike_depth_label(moneyness: str, offset_index: int) -> str:
    """
    moneyness: the bucket scanner.py already assigned ("ITM"/"ATM"/"OTM").
    offset_index: how many strikes away from ATM this one is within its
    bucket (1 = nearest ITM/OTM strike, 2 = one further, per
    config.STRIKE_RANGE) -- scanner.py's resolve_strike_candidates()
    already has this ordering, it's just not labeled today.
    """
    if moneyness == "ATM":
        return "ATM"
    depth = "Deep " if abs(offset_index) >= 2 else ""
    return f"{depth}{moneyness}"


def strike_score(moneyness: str, offset_index: int, delta) -> float:
    """
    0-1: ATM scores highest (best liquidity/probability balance in
    practice), near-the-money ITM/OTM score well, Deep ITM (capital
    inefficient, low leverage) and Deep OTM (low probability, high
    theta risk -- already partly gated by filters.delta_acceptable())
    both score lower. delta, when available, sharpens the OTM read
    further (a "Deep OTM" strike that still has a reasonably healthy
    delta isn't penalized as hard as one with a near-zero delta).
    """
    label = strike_depth_label(moneyness, offset_index)
    base = {
        "ATM": 1.0,
        "ITM": 0.85,
        "OTM": 0.75,
        "Deep ITM": 0.5,
        "Deep OTM": 0.35,
    }.get(label, 0.5)

    if label == "Deep OTM" and delta is not None:
        # abs(delta) close to 0 -> pull score further down; a "Deep OTM"
        # strike with a still-healthy delta (further from 0) is nudged
        # back up rather than flatly penalized.
        base = _clamp01(base + (abs(delta) - 0.15) * 0.5)
    return _clamp01(base)


# ---------------------------------------------------------------------------
# Expiry Analysis Engine
# ---------------------------------------------------------------------------

def expiry_bucket(days_to_expiry_value: int) -> str:
    if days_to_expiry_value is None:
        return "Unknown"
    return "Weekly" if days_to_expiry_value <= EXPIRY_WEEKLY_MAX_DAYS else "Monthly"


def expiry_volatility_label(days_to_expiry_value: int) -> str:
    """Qualitative label only (the actual theta-risk GATING already
    lives in filters.expiry_theta_ok(), reused as-is, not duplicated
    here) -- "Elevated" inside the same near-expiry window that
    function tightens its OTM delta floor for, "Normal" otherwise."""
    if days_to_expiry_value is None:
        return "Unknown"
    return "Elevated" if days_to_expiry_value <= EXPIRY_DAY_THRESHOLD_DAYS else "Normal"


def expiry_score(days_to_expiry_value: int, moneyness: str) -> float:
    """
    0-1: further from expiry scores higher (less theta compression),
    tapering down as expiry approaches -- OTM strikes taper faster
    than ITM/ATM since theta risk compounds specifically for them (see
    filters.expiry_theta_ok()'s docstring), matching this codebase's
    existing OTM-specific theta treatment rather than a single curve
    for every moneyness.
    """
    if days_to_expiry_value is None:
        return 0.5
    ceiling_days = max(EXPIRY_WEEKLY_MAX_DAYS, 1)
    base = _clamp01(days_to_expiry_value / ceiling_days)
    if moneyness == "OTM" and days_to_expiry_value <= EXPIRY_DAY_THRESHOLD_DAYS:
        base *= 0.5  # extra taper -- same near-expiry OTM risk window expiry_theta_ok() gates on
    return base
