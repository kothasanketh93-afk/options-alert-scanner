"""
app/backend/api/routes/diagnostics.py
-----------------------------------------
Part 10, Phase 5 (Runtime Diagnostics) + Phase 6 (Production Completion).

New, additive, read-only routes -- nothing in any other route changes.
"""

from typing import Optional

from fastapi import APIRouter, Query

from production.runtime_diagnostics import (
    generate_runtime_report, uptime_tracker, peak_tracker, availability_tracker,
)
from production.performance_summary import generate_performance_summary
from production.historical_metrics import historical_metrics
from production.service_registry import service_registry
from production.scanner_monitor import scanner_monitor
from production.broker_monitor import broker_monitor
from production.telegram_monitor import telegram_monitor
from production.api_monitor import api_monitor
from production.websocket_monitor import websocket_monitor
from production.dashboard_monitor import dashboard_monitor
from production.resource_monitor import resource_monitor
from production.alert_engine import alert_engine, AlertSources
from production.task_manager import task_manager
from production.health_registry import health_registry

router = APIRouter(prefix="/api/diagnostics", tags=["diagnostics"])


@router.get("/runtime")
def runtime_report():
    """Part 10 Phase 5: uptime, average scan/API time, peak CPU/
    memory, today's alerts/errors/failures/restarts, worker status,
    connection status, health score, availability %. Production score
    is omitted here (needs a production_report.generate_production_report()
    call with config/startup reports this route has no access to at
    request time) -- see PART10_PHASE3_4_MONITORING.md /
    PART10_PHASE5_6.md for wiring it in at startup instead."""
    alert_sources = AlertSources(
        resource_monitor=resource_monitor,
        broker_status=broker_monitor.health,
        telegram_status=telegram_monitor.health,
        scanner_status=scanner_monitor.health,
        api_status=api_monitor.health,
        websocket_status=websocket_monitor.health,
        task_manager=task_manager,
    )
    report = generate_runtime_report(
        scanner_monitor=scanner_monitor, api_monitor=api_monitor,
        resource_monitor=resource_monitor, alert_engine=alert_engine,
        alert_sources=alert_sources, task_manager=task_manager,
        broker_monitor=broker_monitor, websocket_monitor=websocket_monitor,
        health_registry=health_registry,
        uptime_tracker=uptime_tracker, peak_tracker=peak_tracker,
        availability_tracker=availability_tracker,
    )
    return report.to_dict()


@router.get("/performance")
def performance_summary():
    """Part 10 Phase 6: average Scanner/API/Dashboard/Replay/Analytics
    duration."""
    return generate_performance_summary(
        scanner_monitor=scanner_monitor, api_monitor=api_monitor, dashboard_monitor=dashboard_monitor,
    ).to_dict()


@router.get("/history/{metric}")
def metric_history(metric: str, granularity: Optional[str] = Query(
        default=None, description="One of 1_minute, 5_minutes, 15_minutes, 1_hour, daily. Omit for all.")):
    """Part 10 Phase 6: historical metrics rollups. Nothing feeds
    `historical_metrics` automatically yet (see migration notes for
    wiring scan/API/refresh durations into it); this route works as
    soon as anything calls `historical_metrics.record(metric, value)`."""
    all_rollups = historical_metrics.all_rollups(metric)
    if granularity is not None:
        buckets = all_rollups.get(granularity, [])
        return {"metric": metric, "granularity": granularity, "buckets": [b.__dict__ for b in buckets]}
    return {"metric": metric, "rollups": {g: [b.__dict__ for b in bs] for g, bs in all_rollups.items()}}


@router.get("/services")
def service_discovery():
    """Part 10 Phase 6: component dependency map + runtime status,
    plus worker/background-task/monitoring registries."""
    return {
        "dependency_map": service_registry.dependency_map(),
        "runtime_status": service_registry.runtime_status(),
        "workers": service_registry.worker_registry(),
        "monitored_components": service_registry.monitoring_registry(),
    }
