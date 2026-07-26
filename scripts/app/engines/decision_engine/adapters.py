"""
app/engines/decision_engine/adapters.py -- Adapters over EXISTING engines.
------------------------------------------------------------------------------
These wrap output the upstream engines already attached to the trade
dict this cycle -- no recomputation, no new broker/API calls. Each
adapter's is_available() is the "ignore unavailable optional engines"
gate from the spec: if the field it needs isn't on the trade dict,
that engine simply didn't run for this trade (e.g. options
intelligence disabled, or this trade came from the plain scanner
rather than Early Breakout) and the plugin quietly opts out.

FUTURE ENGINES (Market Intelligence, Institutional Intelligence,
Trading Intelligence, Replay, AI Prediction): none exist in this
codebase yet. Per the "never reference modules that do not exist"
rule, no adapter for them is written here -- but they need nothing
more than one more class following this exact pattern (is_available()
checks for the field/module they'd attach, everything else identical)
plus one line in engine.py's build_registry(). That is the whole
extension surface; nothing else in the Decision Engine changes.
"""

from .base import IntelligencePlugin


class MarketSnapshotPlugin(IntelligencePlugin):
    """Reads app/market/snapshot.py's already-computed regime/trend/
    volatility read, passed in via context["snapshot"] by whichever
    caller has one (run_scanner_step/run_early_breakout_step already
    fetch it upstream for the early breakout engine)."""

    name = "market_snapshot"
    default_weight = 0.15

    def __init__(self):
        self._snapshot = None

    def initialize(self, context: dict) -> None:
        self._snapshot = context.get("snapshot")

    def is_available(self, trade: dict) -> bool:
        return bool(self._snapshot)

    def calculate_score(self, trade: dict) -> float:
        snap = self._snapshot or {}
        label = (snap.get("trend_strength_label") or "").lower()
        strength_map = {"strong": 90, "moderate": 65, "weak": 40, "none": 20}
        base = strength_map.get(label, 50)
        vol = (snap.get("volatility_level") or "").lower()
        if vol == "extreme":
            base -= 15
        elif vol == "high":
            base -= 5
        return max(0.0, min(100.0, base))

    def calculate_confidence(self, trade: dict) -> float:
        return 70.0 if self._snapshot else 0.0

    def explain(self, trade: dict) -> str:
        snap = self._snapshot or {}
        return (f"Market regime {snap.get('regime', 'n/a')}, "
                f"{snap.get('trend_strength_label', 'n/a')} trend, "
                f"{snap.get('volatility_level', 'n/a')} volatility")


class EarlyBreakoutPlugin(IntelligencePlugin):
    """Reads app/engines/early_breakout/engine.py's already-computed
    trade_score/base_score/institutional_score/momentum_score/
    smart_money_score -- only present when strategy == 'early_breakout'."""

    name = "early_breakout"
    default_weight = 0.35

    def is_available(self, trade: dict) -> bool:
        return trade.get("strategy") == "early_breakout" and "base_score" in trade

    def calculate_score(self, trade: dict) -> float:
        return float(trade.get("trade_score", 0))

    def calculate_confidence(self, trade: dict) -> float:
        # Blend of the two hard-gated stage scores this engine already
        # requires (>=70 each, config.INSTITUTIONAL_SCORE_MIN /
        # BASE_SCORE_MIN) plus momentum/smart-money as softer signals.
        parts = [
            trade.get("institutional_score"), trade.get("base_score"),
            trade.get("momentum_score"), trade.get("smart_money_score"),
        ]
        parts = [p for p in parts if p is not None]
        return sum(parts) / len(parts) if parts else 0.0

    def validate(self, trade: dict):
        reasons = []
        if trade.get("grade") == "Reject":
            reasons.append("Early Breakout grade is Reject")
        return (len(reasons) == 0, reasons)

    def explain(self, trade: dict) -> str:
        return (f"Early Breakout {trade.get('trade_score', 0)}/100 "
                f"({trade.get('grade', 'n/a')}), institutional "
                f"{trade.get('institutional_score', 'n/a')}, base "
                f"{trade.get('base_score', 'n/a')}")


class TradeScoreEnginePlugin(IntelligencePlugin):
    """Reads trade_score.py's Professional Trade Score Engine output --
    the path used when strategy is the plain scanner (not Early Breakout)."""

    name = "trade_score_engine"
    default_weight = 0.35

    def is_available(self, trade: dict) -> bool:
        return trade.get("strategy", "trade_score") == "trade_score" and "trade_score" in trade

    def calculate_score(self, trade: dict) -> float:
        return float(trade.get("trade_score", 0))

    def calculate_confidence(self, trade: dict) -> float:
        # confidence_score is carried on a 0-10 scale (trade_score / 10)
        # per new_trade_record()/breakout_score.py -- rescale to 0-100.
        return float(trade.get("confidence_score", 0)) * 10.0

    def explain(self, trade: dict) -> str:
        return (f"Trade Score {trade.get('trade_score', 0)}/100 "
                f"({trade.get('probability_label', trade.get('grade', 'n/a'))}), "
                f"probability {trade.get('probability_pct', 'n/a')}%")


class OptionsIntelligencePlugin(IntelligencePlugin):
    """Reads app/engines/options_intelligence/engine.py's Standard
    Output Object, attached at trade['options_intelligence']."""

    name = "options_intelligence"
    default_weight = 0.20

    def is_available(self, trade: dict) -> bool:
        oi = trade.get("options_intelligence")
        return bool(oi and oi.get("enabled"))

    def _oi(self, trade: dict) -> dict:
        return trade.get("options_intelligence") or {}

    def calculate_score(self, trade: dict) -> float:
        return float(self._oi(trade).get("options_intelligence_score", 0))

    def calculate_confidence(self, trade: dict) -> float:
        confidence_label = (self._oi(trade).get("confidence") or "").lower()
        label_map = {"very high": 95, "high": 80, "medium": 60, "low": 35, "very low": 15}
        return label_map.get(confidence_label, self.calculate_score(trade))

    def validate(self, trade: dict):
        oi = self._oi(trade)
        if oi.get("accepted", True):
            return True, []
        return False, list(oi.get("rejection_reasons", []) or ["Options Intelligence rejected"])

    def explain(self, trade: dict) -> str:
        oi = self._oi(trade)
        return (f"Options Intelligence {oi.get('options_intelligence_score', 'n/a')}/100 "
                f"(confidence {oi.get('confidence', 'n/a')}, bias {oi.get('market_bias', 'n/a')})")


def build_default_plugins() -> list:
    """The engines that exist in THIS codebase today. See engine.py's
    build_registry() for how future engines get added alongside these
    without touching anything here."""
    return [
        MarketSnapshotPlugin(),
        EarlyBreakoutPlugin(),
        TradeScoreEnginePlugin(),
        OptionsIntelligencePlugin(),
    ]
