"""
models/signal.py -- The single input contract for QuantEngine.process().

This intentionally uses stdlib dataclasses (not pydantic) so the module
has zero hard third-party dependencies beyond PyYAML. If pydantic is
available in the host environment and preferred for stricter runtime
validation, `Signal.model_validate_dict` gives an equivalent entry point
that can be swapped later without touching call sites -- see
utils/validation.py for the validation actually applied.

Field naming loosely mirrors what a typical NSE options scanner already
computes per strike (trend direction, EMA9/21, VWAP, ATR, OI, IV, etc.)
so mapping a scanner's internal trade dict into a Signal is a thin,
mechanical translation at integration time -- no scanner code changes.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Optional


@dataclass
class Signal:
    # --- Identity / bookkeeping -------------------------------------------------
    symbol: str
    direction: str                       # "CE" or "PE"
    generated_at: float = field(default_factory=lambda: time.time())  # epoch seconds

    # --- Price context -----------------------------------------------------------
    spot_price: Optional[float] = None
    option_price: Optional[float] = None
    strike: Optional[float] = None
    expiry_days: Optional[int] = None    # calendar/trading days to expiry

    # --- Core indicator inputs (per the Part 11 spec) -----------------------------
    trend: Optional[str] = None          # e.g. "UP" / "DOWN" / "FLAT"
    ema_fast: Optional[float] = None     # e.g. EMA9
    ema_slow: Optional[float] = None     # e.g. EMA21
    vwap: Optional[float] = None
    rsi: Optional[float] = None
    adx: Optional[float] = None
    atr: Optional[float] = None
    pcr: Optional[float] = None
    oi: Optional[float] = None
    oi_change_pct: Optional[float] = None
    volume: Optional[float] = None
    avg_volume: Optional[float] = None
    iv: Optional[float] = None
    momentum_pct: Optional[float] = None
    market_breadth: Optional[float] = None       # -1..1 (advancers-decliners ratio-ish)
    breakout_strength: Optional[float] = None    # 0..1, already normalized by caller
    candle_structure: Optional[str] = None       # e.g. "bullish_engulfing", "doji", ...

    # --- Liquidity context ---------------------------------------------------------
    min_oi_threshold: Optional[float] = None
    min_volume_threshold: Optional[float] = None
    spread_pct: Optional[float] = None

    # --- Market-wide context --------------------------------------------------------
    gap_pct: Optional[float] = None              # today's gap vs prior close, %
    is_expiry_day: Optional[bool] = None
    is_news_driven: Optional[bool] = None        # caller-flagged: scheduled news/event window
    index_efficiency_ratio: Optional[float] = None  # broad-index trend cleanliness 0..1

    # --- Freeform passthrough (never inspected by engines, but preserved in results) -
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Signal":
        known = {f for f in cls.__dataclass_fields__}
        clean = {k: v for k, v in data.items() if k in known}
        extra = {k: v for k, v in data.items() if k not in known}
        if extra:
            clean.setdefault("extra", {}).update(extra) if "extra" in clean else clean.update(
                {"extra": extra}
            )
        return cls(**clean)
