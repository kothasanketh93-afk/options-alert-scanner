"""
app/backend/api/routes/health_extended.py
-------------------------------------------
Part 9, item 12: Health Improvements.

A NEW route, /api/health/extended -- the existing /api/health route
(routes/health.py) and its response shape are completely untouched, so
every current consumer of /api/health keeps working exactly as before.
This is additive: adds resource/background-task/broker/telegram/api/
websocket/scan-schedule status on top, via production.health_aggregator.

Providers (broker/telegram/api/websocket/scan_schedule status, plus
the shared resource_monitor/task_manager instances) are not wired up
here -- this route reports "not_configured" for anything main.py or
run_dashboard_api.py hasn't registered yet. See health_aggregator.py's
docstring for why (decoupling: this route must not hard-import
broker.py, ws_manager, etc.).
"""

from fastapi import APIRouter

from production.health_aggregator import get_extended_health
from production.resource_monitor import resource_monitor
from production.task_manager import task_manager

router = APIRouter(prefix="/api/health", tags=["health"])


@router.get("/extended")
def health_extended():
    """Extended system health: everything /api/health reports, plus
    resource-monitor readings, background-task status, and any
    broker/Telegram/API/WebSocket/scan-schedule providers that have
    been registered (see production.health_aggregator.HealthProviders).
    Read-only."""
    return get_extended_health(resource_monitor=resource_monitor, task_manager=task_manager)
