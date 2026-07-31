"""
production/health_aggregator.py
----------------------------------
Part 9, item 12: Health Improvements.

Extends -- without modifying -- app/backend/services/health_service.py.
That module's get_health() (state-branch reachability, circuit
breaker, live-pipeline watchdog, dashboard-process CPU/mem/disk) is
reused as-is via a lazy import (same pattern health_service.py itself
already uses for live_service, to dodge a circular import). This
module adds the pieces Part 9 item 12 asks for that health_service.py
had no source for at the time it was written: background-task status,
resource-monitor alerts, and broker/Telegram/API/WebSocket/scan-
schedule status -- all via optional, decoupled providers (same
register-a-callable pattern as resource_monitor.register_gauge and for
the same reason: this module must not hard-import task_manager,
ws_manager, broker.py, or telegram, since not every process that wants
a health report has all of those loaded).

Wiring this in is opt-in: call get_extended_health() from a NEW route
(see app/backend/api/routes/health.py's existing /api/health, left
completely unchanged) or from a CLI/log line in main.py. Nothing reads
this automatically.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

from production.resource_monitor import ResourceMonitor, AlertLevel
from production.task_manager import TaskManager


ComponentStatusProvider = Callable[[], Dict[str, Any]]


@dataclass
class HealthProviders:
    """Every field is optional -- an unset provider is reported as
    "not_configured" rather than causing an error or being silently
    omitted, so it's obvious from the report itself what isn't wired
    up yet versus what was checked and found healthy."""
    broker_status: Optional[ComponentStatusProvider] = None
    telegram_status: Optional[ComponentStatusProvider] = None
    api_status: Optional[ComponentStatusProvider] = None
    websocket_status: Optional[ComponentStatusProvider] = None
    scan_schedule: Optional[ComponentStatusProvider] = None  # -> {"last_scan_at": ..., "next_scan_at": ...}


def _call_provider(name: str, provider: Optional[ComponentStatusProvider]) -> Dict[str, Any]:
    if provider is None:
        return {"status": "not_configured"}
    try:
        result = provider()
        return {"status": "ok", **(result or {})}
    except Exception as exc:  # noqa: BLE001 - a broken health probe must not break the health report
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}


def _base_health() -> Dict[str, Any]:
    """Lazy import mirrors health_service.py's own _live_pipeline_health()
    pattern: avoids a hard/circular dependency and degrades to an
    empty-but-shaped result if the dashboard's own health_service isn't
    reachable (e.g. called from the plain scanner process, which never
    imports app.backend at all)."""
    try:
        from app.backend.services import health_service
        return health_service.get_health()
    except Exception as exc:  # noqa: BLE001
        return {"status": "unknown", "score": None, "issues": [f"base health unavailable: {exc}"]}


def _resource_section(monitor: Optional[ResourceMonitor]) -> Dict[str, Any]:
    if monitor is None:
        return {"status": "not_configured"}
    snapshot, alerts = monitor.sample_and_evaluate()
    return {
        "status": "ok",
        "memory_pct": snapshot.memory_pct,
        "cpu_pct": snapshot.cpu_pct,
        "disk_pct": snapshot.disk_pct,
        "thread_count": snapshot.thread_count,
        "gauges": snapshot.gauges,
        "alerts": [
            {"resource": a.resource, "level": a.level.value, "value": a.value, "message": a.message}
            for a in alerts
        ],
    }


def _tasks_section(manager: Optional[TaskManager]) -> Dict[str, Any]:
    if manager is None:
        return {"status": "not_configured"}
    statuses = manager.all_status()
    unhealthy = manager.check_health()
    return {
        "status": "ok",
        "running_count": manager.running_count,
        "workers": [
            {
                "name": s.name, "state": s.state.value, "restart_count": s.restart_count,
                "heartbeat_age_seconds": s.heartbeat_age_seconds(), "last_error": s.last_error,
            }
            for s in statuses
        ],
        "unhealthy_count": len(unhealthy),
    }


def get_extended_health(
    resource_monitor: Optional[ResourceMonitor] = None,
    task_manager: Optional[TaskManager] = None,
    providers: Optional[HealthProviders] = None,
) -> Dict[str, Any]:
    """The full Part 9 item 12 picture: everything health_service.py
    already reports, plus resources, background tasks, and whichever
    of broker/telegram/api/websocket/scan-schedule providers were
    supplied. Computes its own overall score/status rather than
    reusing health_service's (which only knows about its own signals)
    -- combines both into one number so a caller reading only this
    function's output gets the complete picture.
    """
    providers = providers or HealthProviders()

    base = _base_health()
    resources = _resource_section(resource_monitor)
    tasks = _tasks_section(task_manager)
    broker = _call_provider("broker", providers.broker_status)
    telegram = _call_provider("telegram", providers.telegram_status)
    api = _call_provider("api", providers.api_status)
    websocket = _call_provider("websocket", providers.websocket_status)
    scan_schedule = _call_provider("scan_schedule", providers.scan_schedule)

    # Same transparent-checklist scoring style as health_service.py's
    # own get_health(): start at 100, dock a fixed amount per problem
    # category, floor at 0.
    score = base.get("score")
    score = 100 if score is None else score
    penalties = 0
    critical_resource_alerts = [
        a for a in resources.get("alerts", []) if a.get("level") == AlertLevel.CRITICAL.value
    ]
    if critical_resource_alerts:
        penalties += 20
    if tasks.get("unhealthy_count", 0) > 0:
        penalties += 20
    for component in (broker, telegram, api, websocket):
        if component.get("status") == "error":
            penalties += 15
    combined_score = max(0, min(score, 100) - penalties)
    overall_status = "healthy" if combined_score == 100 else ("degraded" if combined_score >= 60 else "unhealthy")

    return {
        "overall_status": overall_status,
        "overall_score": combined_score,
        "base": base,
        "resources": resources,
        "background_tasks": tasks,
        "broker": broker,
        "telegram": telegram,
        "api": api,
        "websocket": websocket,
        "scan_schedule": scan_schedule,
    }
