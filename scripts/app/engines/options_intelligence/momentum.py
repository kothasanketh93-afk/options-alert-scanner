"""
momentum.py -- Options Momentum Engine.
------------------------------------------------------------------------------
Premium/OI/Volume/IV momentum, each read as the % change between the
most recent reading and the one before it, per instrument_key, this
process. Same per-PROCESS scope/rationale as option_chain.py's
_vwap_cache and greeks_iv.py's _iv_history -- one GitHub Actions run is
one process, a fresh run starts with no history and every momentum
factor degrades to neutral (0.5) until a second reading exists later
in the SAME run (score_strike() can be called more than once for the
same instrument_key across scan cycles within one run's suppression
window). Cross-run momentum would need state.py's persisted state
file, same follow-up note as greeks_iv.py's IV history.
"""

from config import MOMENTUM_HISTORY_READINGS


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


_history = {}  # instrument_key -> {"premium": [...], "oi": [...], "volume": [...], "iv": [...]}


def reset_history():
    """Test helper -- see greeks_iv.reset_iv_history()'s equivalent."""
    _history.clear()


def _record(instrument_key: str, field: str, value) -> None:
    if instrument_key is None or value is None:
        return
    bucket = _history.setdefault(instrument_key, {})
    series = bucket.setdefault(field, [])
    series.append(float(value))
    if len(series) > MOMENTUM_HISTORY_READINGS:
        del series[0]


def record_reading(instrument_key: str, premium=None, oi=None, volume=None, iv=None) -> None:
    """Call once per strike per scan cycle -- BEFORE reading
    _pct_change() for the same instrument_key/field, same
    read-then-mutate ordering convention as filters.oi_is_building()."""
    _record(instrument_key, "premium", premium)
    _record(instrument_key, "oi", oi)
    _record(instrument_key, "volume", volume)
    _record(instrument_key, "iv", iv)


def _pct_change(instrument_key: str, field: str):
    series = _history.get(instrument_key, {}).get(field, [])
    if len(series) < 2:
        return None
    previous, current = series[-2], series[-1]
    if previous == 0:
        return None
    return (current - previous) / previous


def premium_momentum_pct(instrument_key: str):
    return _pct_change(instrument_key, "premium")


def oi_momentum_pct(instrument_key: str):
    return _pct_change(instrument_key, "oi")


def volume_momentum_pct(instrument_key: str):
    return _pct_change(instrument_key, "volume")


def iv_momentum_pct(instrument_key: str):
    return _pct_change(instrument_key, "iv")


def _signed_component(pct_change, direction: str, ceiling: float = 0.05) -> float:
    """Generic ceiling-scaled 0-1 strength for a signed % change, same
    convention as trade_score.momentum_strength()/vwap_strength() --
    positive change favors CE, negative favors PE."""
    if pct_change is None:
        return 0.5
    signed = pct_change if direction == "CE" else -pct_change
    return _clamp01(signed / ceiling + 0.5) if ceiling else 0.5


def options_momentum_score(instrument_key: str, direction: str) -> float:
    """
    0-1 composite of premium/OI/volume momentum (each weighted equally)
    in the direction that favors `direction`. IV momentum is
    deliberately excluded from the composite (an IV expansion isn't
    inherently bullish or bearish the way premium/OI/volume moves are)
    -- it's still exposed via iv_momentum_pct()/greeks_iv.iv_trend()
    for display, just not folded into this directional score.
    """
    components = [
        _signed_component(premium_momentum_pct(instrument_key), direction),
        _signed_component(oi_momentum_pct(instrument_key), direction, ceiling=0.10),
        _signed_component(volume_momentum_pct(instrument_key), direction, ceiling=0.20),
    ]
    return sum(components) / len(components)
