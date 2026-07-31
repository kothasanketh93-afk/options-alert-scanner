"""
app/engines/early_breakout/models.py -- typed contracts for this engine's
public interface.
------------------------------------------------------------------------------
TypedDict, not a dataclass: every setup dict produced by engine.py flows
straight into main.new_trade_record(), which does setup["key"] dict access,
and main.py itself is out of scope for this pilot (see the top-level
app/__init__.py docstring for why). TypedDict gives real type-checking
value -- a typo in a key name, or a caller that forgets a required field,
is now a type error an IDE/mypy can catch -- with ZERO runtime behavior
change: a TypedDict instance IS a plain dict at runtime, so every existing
`setup["symbol"]`-style access on either side keeps working exactly as
before. A dataclass would have required changing that access pattern
everywhere it's consumed, which is exactly the kind of unbounded, hard-to-
verify ripple effect this pilot is deliberately avoiding.

This is intentionally NOT exhaustive -- it types the one contract that
actually crosses a module boundary (engine.py's setup dict, consumed by
main.py). The internal stage-result dicts (base_detector's, breakout_
detector's, etc.) stay untyped plain dicts for now; typing those has real
value too, but doing it for every internal function on the first pilot
risks scope creep past what one bounded module was supposed to validate.
"""

from typing import TypedDict


class EarlyBreakoutReasons(TypedDict):
    """The human-readable reason strings shown in the Telegram alert
    (telegram_notify.fmt_early_breakout_alert reads this dict directly)."""
    trend: str
    vwap: str
    breakout: str
    follow_through: str
    volume: str
    liquidity: str
    momentum: str
    fair_value_gap: str
    price_action: str
    invalidation: str
    market_context: str


class TradeSetup(TypedDict):
    """
    One qualifying symbol+direction candidate, as produced by
    engine.detect_all_early_breakout_setups() and consumed by
    main.new_trade_record(). Field set mirrors exactly what that
    function reads via setup["key"] / setup.get("key") -- see
    main.py's new_trade_record() docstring for the passthrough-fields
    note explaining why "strategy" / "early_breakout_reasons" /
    "base_score" / "institutional_score" / "momentum_score" /
    "smart_money_score" / "breakout_price" / "entry_zone" /
    "maximum_entry" are optional there (only this engine's setups
    carry them; the original Trade Score Engine's setups don't).
    """
    strategy: str  # always "early_breakout" for setups from this engine
    symbol: str
    direction: str  # "CE" | "PE"
    moneyness: str  # always "ATM" for this engine
    instrument_key: str
    strike: float
    expiry: str
    spot_price: float
    alert_premium: float
    entry_low: float
    entry_high: float
    stop_loss: float
    target_1: float
    target_2: float
    grade: str
    trade_score: int
    probability_pct: int
    probability_label: str
    score_breakdown: list
    confidence_score: float
    risk_score: float
    reward_score: float
    expected_rr: str
    factor_checklist: list
    confidence_stars: str
    confidence_label: str
    risk_rating: str
    risk_label: str
    expected_holding_minutes: tuple
    why_reasons: list
    session: str
    alternatives: list
    early_breakout_reasons: EarlyBreakoutReasons
    base_score: int
    institutional_score: int
    momentum_score: float
    smart_money_score: int
    breakout_price: float
    entry_zone: str
    maximum_entry: float
    fvg_confirmed: bool
    price_action_confirmed: bool


class ScanStats(TypedDict):
    """Returned by engine.get_last_scan_stats()."""
    candidates: int
    rejected: int
