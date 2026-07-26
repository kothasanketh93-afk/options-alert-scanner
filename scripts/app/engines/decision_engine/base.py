"""
app/engines/decision_engine/base.py -- Plugin contract + registry.
------------------------------------------------------------------------------
Every "Intelligence Engine" the Decision Engine consumes (existing or
future) implements IntelligencePlugin. The engine itself is a thin
orchestrator over whatever plugins the registry finds available --
adding a new intelligence source (e.g. a future Institutional
Intelligence or AI Prediction engine) means writing one adapter class
and registering it; engine.py never needs to change.

DISCOVERY, NOT DUPLICATION: plugins here do NOT recompute anything the
existing engines already scored. Each adapter reads the fields that
engine already attached to the trade dict this cycle (trade_score.py's
Trade Score Engine fields, early_breakout's early_breakout_reasons/
base_score/institutional_score/..., options_intelligence's
options_intelligence dict, app/market/snapshot.py's snapshot). If a
field is absent -- because that engine didn't run for this trade, or
because a future engine doesn't exist yet -- the adapter reports
itself unavailable and the registry skips it silently. Nothing here
ever calls a broker, re-fetches a candle, or re-runs scoring math that
already ran upstream.
"""

from abc import ABC, abstractmethod
from logging_setup import get_logger

_logger = get_logger("decision_engine.registry")


class IntelligencePlugin(ABC):
    """
    Common interface every Decision-Engine-facing intelligence source
    implements. Adapters over EXISTING engines (Market Snapshot, Early
    Breakout, Options Intelligence) live in adapters.py. Future engines
    (Institutional Intelligence, Trading Intelligence, Replay, AI
    Prediction, ...) only need to subclass this and call
    registry.register(); the Decision Engine picks them up automatically.
    """

    #: Short, stable key used in the Decision Object's per-engine
    #: breakdown (e.g. "options_intelligence", "early_breakout").
    name = "unnamed_plugin"

    #: Relative weight (0-1) this plugin contributes to the blended
    #: confidence score, before automatic renormalization for whichever
    #: plugins are actually available this cycle. Configurable per
    #: engine via config.DECISION_ENGINE_PLUGIN_WEIGHTS -- this is only
    #: the fallback default.
    default_weight = 0.1

    def initialize(self, context: dict) -> None:
        """Optional setup hook. context = {"trade": trade, "config": config_module}."""
        return None

    @abstractmethod
    def is_available(self, trade: dict) -> bool:
        """True if this plugin has real data to evaluate for this trade
        (i.e. the upstream engine actually ran and attached its fields
        this cycle). Returning False is not an error -- it's how
        "ignore unavailable optional engines" is implemented."""
        raise NotImplementedError

    @abstractmethod
    def calculate_score(self, trade: dict) -> float:
        """0-100 raw quality score from this engine's perspective."""
        raise NotImplementedError

    @abstractmethod
    def calculate_confidence(self, trade: dict) -> float:
        """0-100 confidence this engine has in its own read of the trade."""
        raise NotImplementedError

    def validate(self, trade: dict) -> tuple[bool, list[str]]:
        """(passed, reasons). Default: always passes with no reasons --
        override for a plugin that can outright reject a trade."""
        return True, []

    def explain(self, trade: dict) -> str:
        """One short human-readable line for the Trade Explanation Engine."""
        return f"{self.name}: score {self.calculate_score(trade):.0f}/100"

    def shutdown(self) -> None:
        return None


class PluginRegistry:
    """
    Holds every registered plugin and resolves, per trade, which ones
    are actually available this cycle. A fresh registry is built per
    evaluate() call (registration is cheap, module-level state isn't
    shared across the async/parallel evaluation config.py allows for) --
    see engine.py.
    """

    def __init__(self):
        self._plugins: list[IntelligencePlugin] = []

    def register(self, plugin: IntelligencePlugin) -> None:
        self._plugins.append(plugin)

    def discover(self, trade: dict, context: dict) -> list[IntelligencePlugin]:
        """
        Returns the subset of registered plugins that are available for
        this trade, initialized and ready. Unavailable plugins are
        logged at debug level and skipped -- never raise.
        """
        available = []
        for plugin in self._plugins:
            try:
                plugin.initialize(context)
                if not plugin.is_available(trade):
                    _logger.debug(f"DECISION_ENGINE_PLUGIN_UNAVAILABLE {plugin.name}")
                    continue
                available.append(plugin)
            except Exception as exc:
                # An optional engine misbehaving must never take down the
                # Decision Engine or block an otherwise-valid trade.
                _logger.error(f"DECISION_ENGINE_PLUGIN_ERROR {plugin.name}: {type(exc).__name__}: {exc}", exc_info=True)
                continue
        return available

    def all_names(self) -> list[str]:
        return [p.name for p in self._plugins]
