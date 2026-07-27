"""
production/api_monitor.py
----------------------------
Part 10, Phase 3: API Monitoring.

Per-endpoint metrics -- request/success/failure counts, average/p95/
max/min response time, concurrent requests, slow requests, rejected
requests, validation errors, rate-limit events -- built on the same
RollingWindow used by scanner_monitor.py/broker_monitor.py/
telegram_monitor.py, keyed by endpoint name.

Split the same way production/api_protection.py is split:

1. APIMonitor -- pure Python, no FastAPI/Starlette dependency.
   record_request()/record_rejected()/record_validation_error()/
   record_rate_limited() can be called from anywhere (a route, a
   test, a script) without any web framework installed.
2. APIMonitoringMiddleware -- an optional Starlette ASGI middleware
   that calls APIMonitor automatically for every request, mirroring
   RequestIDMiddleware/TimeoutMiddleware's lazy-import-guarded pattern
   so this module still imports cleanly where starlette isn't
   installed (e.g. the ephemeral scanner process, which never loads
   this module at all in practice).

Reuses api_protection.py's error_envelope shape for nothing new --
this module only *observes* requests/responses, it never changes a
response body or status code, so it is safe to add to app.py without
touching route behavior, matching Part 9/10's "purely additive
monitoring" rule.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Dict, Optional

from production.metrics_utils import RollingWindow
from production.health_states import ComponentHealth, HealthState

# A response slower than this counts as a "slow request" for that endpoint.
DEFAULT_SLOW_REQUEST_MS = 1000.0


@dataclass
class EndpointSnapshot:
    endpoint: str
    request_count: int
    success_count: int
    failure_count: int
    avg_response_time_ms: Optional[float]
    p95_response_time_ms: Optional[float]
    max_response_time_ms: Optional[float]
    min_response_time_ms: Optional[float]
    concurrent_requests: int
    slow_requests: int
    rejected_requests: int
    validation_errors: int
    rate_limit_events: int


class _EndpointCounters:
    """One instance per endpoint name -- created lazily on first use,
    so /api/monitoring/api only ever lists endpoints that have
    actually been called, not every route FastAPI happens to define."""

    __slots__ = (
        "response_times", "request_count", "success_count", "failure_count",
        "concurrent_requests", "slow_requests", "rejected_requests",
        "validation_errors", "rate_limit_events",
    )

    def __init__(self):
        self.response_times = RollingWindow(max_samples=300)
        self.request_count = 0
        self.success_count = 0
        self.failure_count = 0
        self.concurrent_requests = 0
        self.slow_requests = 0
        self.rejected_requests = 0
        self.validation_errors = 0
        self.rate_limit_events = 0


class APIMonitor:
    def __init__(self, slow_request_ms: float = DEFAULT_SLOW_REQUEST_MS):
        self._lock = threading.Lock()
        self._endpoints: Dict[str, _EndpointCounters] = {}
        self.slow_request_ms = slow_request_ms

    def _get(self, endpoint: str) -> _EndpointCounters:
        counters = self._endpoints.get(endpoint)
        if counters is None:
            counters = _EndpointCounters()
            self._endpoints[endpoint] = counters
        return counters

    def request_started(self, endpoint: str) -> None:
        with self._lock:
            self._get(endpoint).concurrent_requests += 1

    def record_request(self, endpoint: str, duration_ms: float, success: bool) -> None:
        """Call once per completed request, after request_started()."""
        with self._lock:
            c = self._get(endpoint)
            c.concurrent_requests = max(0, c.concurrent_requests - 1)
            c.request_count += 1
            c.response_times.record(duration_ms)
            if success:
                c.success_count += 1
            else:
                c.failure_count += 1
            if duration_ms > self.slow_request_ms:
                c.slow_requests += 1

    def record_rejected(self, endpoint: str) -> None:
        with self._lock:
            self._get(endpoint).rejected_requests += 1

    def record_validation_error(self, endpoint: str) -> None:
        with self._lock:
            self._get(endpoint).validation_errors += 1

    def record_rate_limited(self, endpoint: str) -> None:
        with self._lock:
            self._get(endpoint).rate_limit_events += 1

    def endpoint_names(self) -> list:
        with self._lock:
            return sorted(self._endpoints.keys())

    def snapshot(self, endpoint: str) -> Optional[EndpointSnapshot]:
        with self._lock:
            c = self._endpoints.get(endpoint)
            if c is None:
                return None
            stats = c.response_times.stats()
            return EndpointSnapshot(
                endpoint=endpoint, request_count=c.request_count,
                success_count=c.success_count, failure_count=c.failure_count,
                avg_response_time_ms=stats.average, p95_response_time_ms=stats.p95,
                max_response_time_ms=stats.maximum, min_response_time_ms=stats.minimum,
                concurrent_requests=c.concurrent_requests, slow_requests=c.slow_requests,
                rejected_requests=c.rejected_requests, validation_errors=c.validation_errors,
                rate_limit_events=c.rate_limit_events,
            )

    def all_snapshots(self) -> Dict[str, EndpointSnapshot]:
        return {name: self.snapshot(name) for name in self.endpoint_names()}

    def totals(self) -> Dict[str, int]:
        """Process-wide roll-up across every endpoint -- used by
        health() and by dashboard_monitor.py, which needs "total API
        requests/errors" without caring which endpoint they hit."""
        snapshots = self.all_snapshots()
        return {
            "request_count": sum(s.request_count for s in snapshots.values()),
            "failure_count": sum(s.failure_count for s in snapshots.values()),
            "slow_requests": sum(s.slow_requests for s in snapshots.values()),
            "rejected_requests": sum(s.rejected_requests for s in snapshots.values()),
            "validation_errors": sum(s.validation_errors for s in snapshots.values()),
            "rate_limit_events": sum(s.rate_limit_events for s in snapshots.values()),
        }

    def health(self) -> ComponentHealth:
        totals = self.totals()
        state = HealthState.HEALTHY
        reasons = []
        if totals["request_count"] > 0 and (totals["failure_count"] / totals["request_count"]) > 0.1:
            state = HealthState.DEGRADED
            reasons.append(f"{totals['failure_count']}/{totals['request_count']} requests failed")
        if totals["rejected_requests"] > 0:
            state = max(state, HealthState.WARNING)
            reasons.append(f"{totals['rejected_requests']} requests rejected")
        message = "; ".join(reasons) if reasons else "api nominal"
        return ComponentHealth(name="fastapi", state=state, message=message, metrics=totals)

    def status(self) -> Dict[str, object]:
        """Shape expected by health_aggregator.HealthProviders.api_status."""
        return self.totals()


# Process-wide default instance.
api_monitor = APIMonitor()


# --------------------------------------------------------------------
# Optional ASGI middleware (requires Starlette) -- same lazy-import
# guard as production.api_protection so this module still imports
# cleanly where starlette isn't installed.
# --------------------------------------------------------------------
try:
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.requests import Request
    _STARLETTE_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised where starlette isn't installed
    BaseHTTPMiddleware = object  # type: ignore[assignment,misc]
    _STARLETTE_AVAILABLE = False


if _STARLETTE_AVAILABLE:

    class APIMonitoringMiddleware(BaseHTTPMiddleware):
        """Records every request against `monitor` (default: the
        module-level api_monitor) keyed by request.url.path. Runs
        outside RequestIDMiddleware/TimeoutMiddleware in app.py (added
        first, see migration notes) so its timing includes time spent
        in those middlewares too -- the number a client actually
        experienced, not just route-handler time.

        A 4xx/5xx response is recorded as success=False; a raised
        exception is recorded as success=False with status 500 before
        being re-raised, so app.py's own unhandled_exception_handler
        still runs unchanged."""

        def __init__(self, app, monitor: Optional[APIMonitor] = None):
            super().__init__(app)
            self.monitor = monitor or api_monitor

        async def dispatch(self, request: Request, call_next):
            endpoint = request.url.path
            self.monitor.request_started(endpoint)
            start = time.time()
            try:
                response = await call_next(request)
            except Exception:
                self.monitor.record_request(endpoint, (time.time() - start) * 1000.0, success=False)
                raise
            duration_ms = (time.time() - start) * 1000.0
            success = response.status_code < 400
            self.monitor.record_request(endpoint, duration_ms, success=success)
            if response.status_code == 422:
                self.monitor.record_validation_error(endpoint)
            elif response.status_code == 429:
                self.monitor.record_rate_limited(endpoint)
            elif response.status_code in (403, 401):
                self.monitor.record_rejected(endpoint)
            return response

else:  # pragma: no cover - only reached where starlette isn't installed
    APIMonitoringMiddleware = None  # type: ignore[assignment]
