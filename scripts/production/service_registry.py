"""
production/service_registry.py
----------------------------------
Part 10, Phase 6: Service Discovery.

A thin registration + dependency-map layer on top of what already
exists, not a second monitoring system:

  - Component registration / runtime component status: wraps
    production.health_registry (Part 10 item 1), which already holds
    "name -> health-check callable". This module adds a declared
    dependency list per component (health_registry itself has no
    notion of "depends on") and a `runtime_status()` view that
    combines both.
  - Background task registry / Worker registry: this codebase's
    background workers (production.task_manager.TaskManager) already
    ARE the background task registry -- `task_manager.all_status()`
    lists every registered worker with its state, restart count, and
    heartbeat age. Phase 6 lists "Background task registry" and
    "Worker registry" as two separate bullets; in this codebase they
    are the same underlying registry (there is no separate notion of
    a "task" that isn't run by a "worker"), so both accessors below
    read from the same TaskManager instance rather than maintaining
    two parallel lists that could drift out of sync.
  - Monitoring registry: the set of component names currently
    registered with health_registry, i.e. "what is actually being
    monitored right now" -- useful for a startup/runtime report to
    show what it did and didn't check (same "not assessed vs
    assessed" transparency rule as every other Part 9/10 report).

Auto-registration helper: `register_known_components()` registers the
five Phase 3 monitors (scanner/broker/telegram/websocket/api/dashboard)
health-check callables with health_registry AND records their
dependency edges here, in one call -- this is what "automatic
component registration" means in practice for this project: one
function, called once at startup, instead of six separate
`health_registry.register(...)` call sites scattered across app.py/
main.py.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from production.health_registry import HealthRegistry


@dataclass
class ComponentInfo:
    name: str
    category: str  # e.g. "monitor", "worker", "external_service"
    depends_on: List[str] = field(default_factory=list)


class ServiceRegistry:
    def __init__(self, health_registry: Optional[HealthRegistry] = None,
                 task_manager: Optional[object] = None):
        self._lock = threading.Lock()
        self._components: Dict[str, ComponentInfo] = {}
        self.health_registry = health_registry
        self.task_manager = task_manager

    # -- registration -----------------------------------------------------------
    def register_component(self, name: str, category: str, depends_on: Optional[List[str]] = None) -> None:
        with self._lock:
            self._components[name] = ComponentInfo(name=name, category=category, depends_on=list(depends_on or []))

    def unregister_component(self, name: str) -> None:
        with self._lock:
            self._components.pop(name, None)

    def register_known_components(
        self,
        scanner_status: Optional[Callable] = None,
        broker_status: Optional[Callable] = None,
        telegram_status: Optional[Callable] = None,
        websocket_status: Optional[Callable] = None,
        api_status: Optional[Callable] = None,
        dashboard_status: Optional[Callable] = None,
    ) -> None:
        """Registers each supplied `.health()` callable with
        self.health_registry (if one was given) and declares the
        dependency edges that are actually true for this project:
        the dashboard depends on the websocket and API layers to
        serve live data; the API layer's own health in turn reflects
        whatever it fans out to (scanner/broker/telegram), which is
        why those three have no declared dependents here -- they are
        the leaves of the map. Any argument left out is simply not
        registered, matching every other Part 9/10 "opt-in" pattern."""
        entries = (
            ("scanner", scanner_status, []),
            ("broker", broker_status, []),
            ("telegram", telegram_status, []),
            ("websocket", websocket_status, []),
            ("fastapi", api_status, ["scanner", "broker", "telegram"]),
            ("dashboard", dashboard_status, ["websocket", "fastapi"]),
        )
        for name, getter, deps in entries:
            if getter is None:
                continue
            self.register_component(name, category="monitor", depends_on=deps)
            if self.health_registry is not None:
                self.health_registry.register(name, getter)

    # -- views --------------------------------------------------------------------
    def dependency_map(self) -> Dict[str, List[str]]:
        with self._lock:
            return {name: list(info.depends_on) for name, info in self._components.items()}

    def registered_components(self) -> List[ComponentInfo]:
        with self._lock:
            return list(self._components.values())

    def runtime_status(self) -> Dict[str, Dict[str, object]]:
        """One entry per registered component: its declared
        dependencies plus its current health (via health_registry, if
        this instance has one), so a caller can see both the map and
        live state in one call."""
        with self._lock:
            components = dict(self._components)
        health_by_name = {}
        if self.health_registry is not None:
            try:
                health_by_name = self.health_registry.check_all()
            except Exception:  # noqa: BLE001
                health_by_name = {}
        result = {}
        for name, info in components.items():
            health = health_by_name.get(name)
            result[name] = {
                "category": info.category,
                "depends_on": list(info.depends_on),
                "state": str(health.state) if health is not None else "NOT_MONITORED",
                "message": health.message if health is not None else "no health provider registered",
            }
        return result

    # -- background task / worker registry (same underlying data) -----------------
    def background_task_registry(self) -> List[Dict[str, object]]:
        return self._worker_snapshot()

    def worker_registry(self) -> List[Dict[str, object]]:
        return self._worker_snapshot()

    def _worker_snapshot(self) -> List[Dict[str, object]]:
        if self.task_manager is None:
            return []
        return [
            {
                "name": s.name, "state": s.state.value, "restart_count": s.restart_count,
                "heartbeat_age_seconds": s.heartbeat_age_seconds(), "last_error": s.last_error,
            }
            for s in self.task_manager.all_status()
        ]

    # -- monitoring registry ---------------------------------------------------------
    def monitoring_registry(self) -> List[str]:
        """Names currently registered with health_registry -- "what is
        actually being monitored right now", independent of what's in
        `self._components` (a component could be declared here without
        a live health provider, e.g. before startup wiring runs)."""
        if self.health_registry is None:
            return []
        return self.health_registry.registered_names()


# Process-wide default instance. Callers typically do, once at
# startup:
#   service_registry.health_registry = health_registry  (the shared one)
#   service_registry.task_manager = task_manager
#   service_registry.register_known_components(scanner_status=..., ...)
service_registry = ServiceRegistry()
