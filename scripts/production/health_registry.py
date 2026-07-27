"""
production/health_registry.py
--------------------------------
Part 10, item 1: Health Monitoring.

The aggregator that turns "N independent things to monitor" (Scanner,
Decision Engine, Replay Engine, Analytics Engine, Dashboard, FastAPI,
Broker, Telegram, WebSocket, Background Workers) into one overall
HealthState plus a per-component breakdown -- via the same decoupled
register-a-callable pattern used throughout Part 9
(resource_monitor.register_gauge, health_aggregator.HealthProviders):
this module never imports scanner.py, decision_engine, broker.py, etc.
directly. Each component's own code (or main.py / run_dashboard_api.py
at startup) registers a zero-arg callable that returns a
ComponentHealth; this module just calls them and combines the results.

A provider that isn't registered is reported as HealthState.OFFLINE
with message "not monitored" -- distinct from a provider that IS
registered but reports trouble (WARNING/DEGRADED/CRITICAL), so a
reader can tell "nobody's watching this" apart from "someone's
watching this and it's broken."
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from production.health_states import ComponentHealth, HealthState, combine_components

# The Part 10 item 1 component list -- registering under a name
# outside this list is allowed (extensible), but these are the
# well-known keys other code/tests can rely on existing in
# check_all()'s output even before anything registers for them.
KNOWN_COMPONENTS = (
    "scanner", "decision_engine", "replay_engine", "analytics_engine",
    "dashboard", "fastapi", "broker", "telegram", "websocket", "background_workers",
)

HealthProvider = Callable[[], ComponentHealth]


class HealthRegistry:
    def __init__(self):
        self._providers: Dict[str, HealthProvider] = {}
        self._lock = threading.RLock()

    def register(self, name: str, provider: HealthProvider) -> None:
        with self._lock:
            self._providers[name] = provider

    def unregister(self, name: str) -> None:
        with self._lock:
            self._providers.pop(name, None)

    def registered_names(self) -> List[str]:
        with self._lock:
            return list(self._providers.keys())

    def check(self, name: str) -> ComponentHealth:
        with self._lock:
            provider = self._providers.get(name)
        if provider is None:
            return ComponentHealth(name=name, state=HealthState.OFFLINE, message="not monitored")
        try:
            result = provider()
            if not isinstance(result, ComponentHealth):
                raise TypeError(f"provider for '{name}' returned {type(result).__name__}, not ComponentHealth")
            return result
        except Exception as exc:  # noqa: BLE001 - a broken health check must not break health reporting
            return ComponentHealth(
                name=name, state=HealthState.OFFLINE,
                message=f"health check raised: {type(exc).__name__}: {exc}",
            )

    def check_all(self) -> Dict[str, ComponentHealth]:
        """Every KNOWN_COMPONENTS key is always present in the result
        (as "not monitored" OFFLINE if nothing registered for it),
        plus any extra names someone registered beyond the known list."""
        with self._lock:
            names = set(KNOWN_COMPONENTS) | set(self._providers.keys())
        return {name: self.check(name) for name in sorted(names)}

    def overall_state(self) -> HealthState:
        return combine_components(list(self.check_all().values()))

    def report(self) -> Dict[str, object]:
        components = self.check_all()
        return {
            "overall_state": str(self.overall_state()),
            "components": {name: c.to_dict() for name, c in components.items()},
        }


# Process-wide default instance -- import this rather than
# constructing your own unless you need isolated registrations
# (e.g. per-test).
health_registry = HealthRegistry()
