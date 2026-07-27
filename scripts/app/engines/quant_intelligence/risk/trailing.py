"""
risk/trailing.py -- Trailing stop-loss computation.

`initial_trailing_stop` gives the stop that would apply *at signal
time* (used for display/planning); a live position manager (outside
this module's scope, per "independent module" constraints) would call
`update_trailing_stop` on each new candle/tick to ratchet it forward.
Both are pure functions -- no state held here.
"""

from __future__ import annotations

from typing import Optional


def initial_trailing_stop(entry: float, stop_loss: float) -> float:
    """At signal time, the trailing stop starts equal to the hard stop loss."""
    return stop_loss


def update_trailing_stop(
    direction: str,
    current_price: float,
    entry: float,
    current_stop: float,
    atr: float,
    *,
    activation_rr: float = 1.0,
    atr_multiplier: float = 0.8,
    initial_risk: Optional[float] = None,
) -> float:
    """Ratchet the trailing stop forward once price has moved `activation_rr`
    multiples of the initial risk in the favorable direction. Never moves the
    stop backward (against the position)."""
    is_ce = direction.upper() == "CE"
    risk = initial_risk if initial_risk is not None else abs(entry - current_stop)
    if risk <= 0:
        return current_stop

    favorable_move = (current_price - entry) if is_ce else (entry - current_price)
    if favorable_move < activation_rr * risk:
        return current_stop  # not yet activated

    candidate = (
        current_price - atr * atr_multiplier
        if is_ce
        else current_price + atr * atr_multiplier
    )

    if is_ce:
        return round(max(current_stop, candidate), 2)
    return round(min(current_stop, candidate), 2)
