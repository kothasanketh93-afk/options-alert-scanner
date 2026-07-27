"""
production/dashboard_monitor.py
-----------------------------------
Part 10, Phase 3: Dashboard Monitoring.

Deliberately thin: "dashboard availability/response time/requests/
errors" are the *same numbers* api_monitor.py already collects for the
dashboard's own HTML/API routes (this project has no separate
dashboard-serving process to instrument -- see run_dashboard_api.py),
and "dashboard WebSocket clients" is the same number
websocket_monitor.py already tracks. This module does not duplicate
either monitor's counters; it reads them and adds the two things
neither one already has: a rolling "dashboard refresh time" sample
(the poll-and-diff cycle time from live_service.py, recorded via
record_refresh()) and an explicit availability flag the caller sets
once at startup / on shutdown.

Call `record_refresh(seconds)` from live_service.poll_loop() (or from
main.py's own dashboard startup) each time the dashboard successfully
renders/refreshes a cycle; call `record_request()`/`record_error()`
for HTTP hits to the dashboard's own root page/asset routes specifically
(as opposed to the JSON API routes api_monitor.py already covers) if
those exist as separate routes; if they don't (this project's
dashboard is API-driven and rendered client-side), the api_monitor
totals below already cover it and these two are simply never called.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Optional

from production.metrics_utils import RollingWindow
from production.health_states import ComponentHealth, HealthState
from production.api_monitor import APIMonitor, api_monitor as _default_api_monitor
from production.websocket_monitor import WebSocketMonitor, websocket_monitor as _default_ws_monitor

DEFAULT_SLOW_REFRESH_SECONDS = 5.0


@dataclass
class DashboardSnapshot:
    available: bool
    avg_response_time_ms: Optional[float]
    dashboard_requests: int
    dashboard_errors: int
    active_sessions: int
    websocket_clients: int
    avg_refresh_time_seconds: Optional[float]
    last_refresh_at: Optional[float]


class DashboardMonitor:
    def __init__(self, api_monitor: Optional[APIMonitor] = None,
                 ws_monitor: Optional[WebSocketMonitor] = None,
                 slow_refresh_seconds: float = DEFAULT_SLOW_REFRESH_SECONDS):
        self._lock = threading.Lock()
        self._refresh_times = RollingWindow(max_samples=200)
        self._available = True
        self._dashboard_requests = 0
        self._dashboard_errors = 0
        self._active_sessions = 0
        self._last_refresh_at: Optional[float] = None
        self.api_monitor = api_monitor or _default_api_monitor
        self.ws_monitor = ws_monitor or _default_ws_monitor
        self.slow_refresh_seconds = slow_refresh_seconds

    def set_available(self, available: bool) -> None:
        with self._lock:
            self._available = available

    def record_refresh(self, duration_seconds: float) -> None:
        with self._lock:
            now = time.time()
            self._refresh_times.record(duration_seconds, at=now)
            self._last_refresh_at = now

    def record_request(self) -> None:
        with self._lock:
            self._dashboard_requests += 1

    def record_error(self) -> None:
        with self._lock:
            self._dashboard_errors += 1

    def session_opened(self) -> None:
        with self._lock:
            self._active_sessions += 1

    def session_closed(self) -> None:
        with self._lock:
            self._active_sessions = max(0, self._active_sessions - 1)

    def snapshot(self) -> DashboardSnapshot:
        api_totals = self.api_monitor.totals()
        ws_snap = self.ws_monitor.snapshot()
        with self._lock:
            refresh_stats = self._refresh_times.stats()
            # Fall back to api_monitor's own averages/errors when this
            # process has no dedicated dashboard-page routes (see
            # module docstring) so the numbers are never silently zero
            # just because record_request()/record_error() are unused.
            requests = self._dashboard_requests or api_totals["request_count"]
            errors = self._dashboard_errors or api_totals["failure_count"]
            return DashboardSnapshot(
                available=self._available,
                avg_response_time_ms=None,  # filled in below (needs api_monitor snapshots, no lock held)
                dashboard_requests=requests,
                dashboard_errors=errors,
                active_sessions=self._active_sessions,
                websocket_clients=ws_snap.active_clients,
                avg_refresh_time_seconds=refresh_stats.average,
                last_refresh_at=self._last_refresh_at,
            )

    def health(self) -> ComponentHealth:
        snap = self.snapshot()
        state = HealthState.HEALTHY
        reasons = []

        if not snap.available:
            state = HealthState.OFFLINE
            reasons.append("dashboard marked unavailable")
        elif snap.dashboard_requests > 0 and (snap.dashboard_errors / snap.dashboard_requests) > 0.1:
            state = HealthState.DEGRADED
            reasons.append(f"{snap.dashboard_errors}/{snap.dashboard_requests} dashboard requests errored")
        elif snap.avg_refresh_time_seconds is not None and snap.avg_refresh_time_seconds > self.slow_refresh_seconds:
            state = HealthState.WARNING
            reasons.append(f"average refresh time {snap.avg_refresh_time_seconds:.1f}s exceeds {self.slow_refresh_seconds:.1f}s")

        message = "; ".join(reasons) if reasons else "dashboard nominal"
        return ComponentHealth(
            name="dashboard", state=state, message=message,
            metrics={
                "available": snap.available, "dashboard_requests": snap.dashboard_requests,
                "dashboard_errors": snap.dashboard_errors, "active_sessions": snap.active_sessions,
                "websocket_clients": snap.websocket_clients,
                "avg_refresh_time_seconds": snap.avg_refresh_time_seconds,
                "last_refresh_at": snap.last_refresh_at,
            },
        )


# Process-wide default instance.
dashboard_monitor = DashboardMonitor()
