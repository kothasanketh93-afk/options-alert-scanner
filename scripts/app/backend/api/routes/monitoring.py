"""
app/backend/api/routes/monitoring.py
---------------------------------------
Part 10, Phase 3 (Live Dashboard Monitoring) + Phase 4 (Alerting Engine).

New, additive routes only -- nothing in routes/health.py,
routes/health_extended.py, or any existing route changes. Read-only,
same as every other route in this API (see app.py's docstring).
"""

from typing import Optional

from fastapi import APIRouter, Query

from production.websocket_monitor import websocket_monitor
from production.api_monitor import api_monitor
from production.dashboard_monitor import dashboard_monitor
from production.resource_monitor import resource_monitor
from production.broker_monitor import broker_monitor
from production.telegram_monitor import telegram_monitor
from production.scanner_monitor import scanner_monitor
from production.task_manager import task_manager
from production.alert_engine import alert_engine, AlertSources, AlertSeverity

router = APIRouter(prefix="/api/monitoring", tags=["monitoring"])


@router.get("/websocket")
def websocket_metrics():
    """Part 10 Phase 3: WebSocket Monitoring snapshot."""
    return websocket_monitor.health().to_dict()


@router.get("/api")
def api_metrics(endpoint: Optional[str] = Query(default=None, description="Filter to one endpoint path")):
    """Part 10 Phase 3: API Monitoring. Without `endpoint`, returns
    process-wide totals plus every endpoint tracked so far; with it,
    returns just that endpoint's counters (404-shaped as an empty dict
    rather than an HTTP 404, since "never called yet" is a normal,
    expected state for a brand-new endpoint, not an error)."""
    if endpoint is not None:
        snap = api_monitor.snapshot(endpoint)
        return snap.__dict__ if snap is not None else {}
    return {
        "totals": api_monitor.totals(),
        "endpoints": {name: s.__dict__ for name, s in api_monitor.all_snapshots().items()},
    }


@router.get("/dashboard")
def dashboard_metrics():
    """Part 10 Phase 3: Dashboard Monitoring snapshot."""
    return dashboard_monitor.health().to_dict()


@router.get("/alerts")
def alerts(min_severity: Optional[str] = Query(
    default=None, description="One of INFO, WARNING, ERROR, CRITICAL")):
    """Part 10 Phase 4: runs every alert rule once against the current
    monitor state (resource/broker/telegram/scanner/api/websocket/
    workers) and returns what fired this call, plus full history and
    the alerts still 'active' (fired within the current cooldown
    window). Evaluating on every GET (rather than only from a
    background loop) means this route always reflects the latest
    numbers even if nothing else has called evaluate() recently --
    see MIGRATION_NOTES.md for wiring a periodic background
    evaluation too, which this route does not replace."""
    sources = AlertSources(
        resource_monitor=resource_monitor,
        broker_status=broker_monitor.health,
        telegram_status=telegram_monitor.health,
        scanner_status=scanner_monitor.health,
        api_status=api_monitor.health,
        websocket_status=websocket_monitor.health,
        task_manager=task_manager,
    )
    fired = alert_engine.evaluate(sources)

    severity_filter = None
    if min_severity is not None:
        try:
            severity_filter = AlertSeverity[min_severity.upper()]
        except KeyError:
            severity_filter = None

    return {
        "fired_this_call": [a.to_dict() for a in fired],
        "active": [a.to_dict() for a in alert_engine.active()],
        "history": [a.to_dict() for a in alert_engine.history(severity_at_least=severity_filter)],
    }
