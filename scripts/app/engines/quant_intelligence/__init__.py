"""
quant_intelligence -- Part 11: Quant Intelligence Engine.

Independent, self-contained module for the NSE Options Scanner. Does
NOT import from, modify, or depend on the host scanner in any way.

Public API (per the integration spec -- this is the *only* integration
point the host application needs):

    from quant_intelligence import QuantEngine, Signal

    signal = Signal(symbol="RELIANCE", direction="CE", ...)
    result = QuantEngine.process(signal)

    # later, once the trade closes:
    QuantEngine.record_outcome(result, outcome="WIN", exit_price=123.4)
"""

from .facade import QuantEngine, QuantEngineImpl
from .models.result import (
    Explanation,
    PatternMatch,
    ProbabilityResult,
    QuantResult,
    RegimeResult,
    RiskResult,
    SignalQualityResult,
)
from .models.signal import Signal

__all__ = [
    "QuantEngine",
    "QuantEngineImpl",
    "Signal",
    "QuantResult",
    "ProbabilityResult",
    "SignalQualityResult",
    "RegimeResult",
    "RiskResult",
    "PatternMatch",
    "Explanation",
]

__version__ = "1.0.0"
