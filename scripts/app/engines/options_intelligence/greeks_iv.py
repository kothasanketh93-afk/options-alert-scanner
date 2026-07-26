"""
greeks_iv.py -- Greeks Engine, Implied Volatility Engine.
------------------------------------------------------------------------------
Delta itself is already extracted and gated upstream (option_chain.py's
_extract_leg(), filters.delta_acceptable()/expiry_theta_ok()) -- this
module does NOT re-decide delta acceptability, it only reads
gamma/theta/vega (added to _extract_leg()'s return dict alongside the
existing keys -- additive, nothing removed) to score risk/decay/
exposure, and adds an IV read that didn't exist anywhere in the
codebase before Part 5.

Both gamma/theta/vega and IV can legitimately be None for a given leg
on either broker (see option_chain.py's docstring on Angel One's
Greeks-endpoint gaps, and upstox.py only ever populating IV, not
gamma/theta/vega) -- every function here treats None as "no read",
scoring it neutral (0.5) rather than 0.0 (which would read as
"actively bad" for data that's simply absent).
"""

from config import (
    GAMMA_RISK_CEILING, THETA_DECAY_CEILING_PCT, VEGA_EXPOSURE_CEILING,
    IV_ABNORMAL_LOW_PCT, IV_ABNORMAL_HIGH_PCT, IV_PERCENTILE_LOOKBACK_READINGS,
)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


# ---------------------------------------------------------------------------
# Greeks Engine
# ---------------------------------------------------------------------------

def gamma_component(gamma) -> float:
    """Higher gamma = faster-changing delta = more convexity but more
    whipsaw risk. Read as a RISK level (higher gamma -> higher number),
    the caller inverts it into the composite Greeks Score."""
    if gamma is None:
        return 0.5
    return _clamp01(abs(gamma) / GAMMA_RISK_CEILING) if GAMMA_RISK_CEILING else 0.5


def theta_component(theta, premium: float) -> float:
    """|theta| as a fraction of premium, per day -- how much of this
    contract's value decays if nothing moves today. Also a RISK
    level."""
    if theta is None or not premium:
        return 0.5
    decay_pct = abs(theta) / premium
    return _clamp01(decay_pct / THETA_DECAY_CEILING_PCT) if THETA_DECAY_CEILING_PCT else 0.5


def vega_component(vega) -> float:
    """Vega exposure -- how much premium moves per 1-point IV change.
    Not inherently good or bad (a directional IV view could WANT high
    vega), so this is reported as an exposure magnitude, not folded
    into the risk direction the way gamma/theta are."""
    if vega is None:
        return 0.5
    return _clamp01(abs(vega) / VEGA_EXPOSURE_CEILING) if VEGA_EXPOSURE_CEILING else 0.5


def greeks_score(gamma, theta, premium: float, vega) -> float:
    """
    0-1 composite: LOWER gamma/theta risk -> higher score (this is a
    "how favorable are the Greeks" read, not a raw risk magnitude), a
    neutral treatment of vega exposure (an informational factor, not
    scored for/against). gamma/theta each contribute 40%, vega's
    presence-as-data-quality contributes 20% (missing Greeks entirely
    is itself a weaker read than having them, even if what they'd say
    is unknown).
    """
    gamma_risk = gamma_component(gamma)
    theta_risk = theta_component(theta, premium)
    vega_known = 1.0 if vega is not None else 0.5
    return _clamp01(0.4 * (1.0 - gamma_risk) + 0.4 * (1.0 - theta_risk) + 0.2 * vega_known)


def risk_rating(gamma, theta, premium: float) -> tuple:
    """Emoji/label pair, same three-bucket convention as
    trade_score.risk_emoji_label() and grading.py's old risk_rating()
    -- driven off this module's own gamma/theta risk reads."""
    combined = (gamma_component(gamma) + theta_component(theta, premium)) / 2
    if combined <= 0.33:
        return "🟢", "Low"
    if combined <= 0.66:
        return "🟡", "Medium"
    return "🔴", "High"


# ---------------------------------------------------------------------------
# Implied Volatility Engine
# ---------------------------------------------------------------------------

# Per-PROCESS IV history, same scope/rationale as option_chain.py's
# _vwap_cache/_expiry_cache/_ltp_cache -- one GitHub Actions run = one
# process, so this naturally resets every run. Cross-run/cross-day IV
# percentile would need state.py's persisted daily state file (see
# that module's probability_series()/probability_stats() for the
# existing pattern) -- deliberately NOT wired up here to avoid
# reaching into state.py's schema for Part 5; iv_percentile()/iv_rank()
# below degrade to "insufficient history" (neutral 0.5) rather than
# guessing, exactly like every other fail-soft read in this codebase,
# until that follow-up is done.
_iv_history = {}


def reset_iv_history():
    """Test/process-boundary helper -- mirrors option_chain.py not
    exposing a reset for its own per-process caches, but this one is
    useful for unit tests that need a clean slate between cases."""
    _iv_history.clear()


def record_iv_reading(instrument_key: str, iv) -> None:
    if iv is None or instrument_key is None:
        return
    history = _iv_history.setdefault(instrument_key, [])
    history.append(float(iv))
    if len(history) > IV_PERCENTILE_LOOKBACK_READINGS:
        del history[0]


def is_iv_abnormal(iv) -> bool:
    """True if IV is outside a sane band -- almost always a stale or
    garbage quote (a dead/illiquid leg), not a genuinely "very cheap"
    or "very rich" option. Reject engine uses this directly."""
    if iv is None:
        return False  # absent isn't "abnormal", just unknown -- see module docstring
    return iv < IV_ABNORMAL_LOW_PCT or iv > IV_ABNORMAL_HIGH_PCT


def iv_percentile(instrument_key: str, current_iv) -> float:
    """Percentile (0-1) of current_iv within this run's own in-process
    history for this instrument_key. Returns 0.5 (neutral) with fewer
    than 2 readings -- there's nothing to rank against yet."""
    if current_iv is None:
        return 0.5
    history = _iv_history.get(instrument_key, [])
    if len(history) < 2:
        return 0.5
    below_or_equal = sum(1 for v in history if v <= current_iv)
    return below_or_equal / len(history)


def iv_rank(instrument_key: str, current_iv) -> float:
    """(current - min) / (max - min) over this run's own history --
    the classic IV Rank formula, same neutral-on-insufficient-data
    fallback as iv_percentile()."""
    if current_iv is None:
        return 0.5
    history = _iv_history.get(instrument_key, [])
    if len(history) < 2:
        return 0.5
    lo, hi = min(history), max(history)
    if hi <= lo:
        return 0.5
    return _clamp01((current_iv - lo) / (hi - lo))


def iv_trend(instrument_key: str) -> str:
    """"Expansion" / "Contraction" / "Stable" off the last two
    readings recorded this run -- same >=2-point-move-counts-as-real
    convention as trade_score.probability_trend()."""
    history = _iv_history.get(instrument_key, [])
    if len(history) < 2:
        return "Stable"
    delta = history[-1] - history[-2]
    if delta >= 0.01:
        return "Expansion"
    if delta <= -0.01:
        return "Contraction"
    return "Stable"


def iv_score(instrument_key: str, current_iv) -> float:
    """
    0-1: penalizes abnormal IV outright (near 0.0), otherwise reads as
    "moderate IV, not at an extreme percentile" -- an IV sitting near
    either extreme of this run's own observed range scores lower than
    one sitting mid-range, since both very-low and very-high IV
    percentile carry their own distinct risk (cheap-for-a-reason vs.
    about-to-mean-revert).
    """
    if current_iv is None:
        return 0.5
    if is_iv_abnormal(current_iv):
        return 0.05
    percentile = iv_percentile(instrument_key, current_iv)
    # 1.0 at the 50th percentile, tapering to 0.0 at either extreme.
    return _clamp01(1.0 - abs(percentile - 0.5) * 2)
