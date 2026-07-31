"""
production/runtime_diagnostics.py
-------------------------------------
Part 10, Phase 5: Runtime Diagnostics.

Two reports, both assembled purely from data other Part 9/10 modules
already collect -- this module owns no monitoring state of its own
except the three small trackers below that nothing else has a reason
to keep (process start time, peak CPU/memory, and a rolling
healthy/unhealthy sample history for an availability percentage).

  - generate_startup_report()  -- Environment/Configuration/
    Dependencies (wraps the existing production.startup_validator
    report -- Part 9 item 4 -- rather than re-implementing those
    checks) plus a point-in-time Broker/Telegram/Scanner/Dashboard/
    Health snapshot from the Phase 1-3 monitors. Meant to be generated
    and logged once, right after startup validation passes.

  - generate_runtime_report()  -- uptime, average scan/API time
    (scanner_monitor / api_monitor), peak CPU/memory (this module's
    own PeakTracker, fed by resource_monitor samples), today's alerts/
    errors/failures/restarts, worker status (task_manager), connection
    status (broker/websocket), a health score (health_registry), a
    production score (production_report, if supplied), and an
    availability % (this module's own AvailabilityTracker). Meant to
    be generated periodically (e.g. once per scan cycle, or from a
    dashboard route) -- see MIGRATION_NOTES for wiring either one in.

Same decoupled pattern as everything else in Part 9/10: every input is
an already-existing monitor instance or callable passed in, optional,
"not assessed" rather than an error when omitted. This module never
imports scanner.py, broker.py, main.py, or any engine.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from production.health_states import HealthState
from production.startup_validator import StartupReport


# --------------------------------------------------------------------
# Small trackers this module owns (nothing else in Part 9/10 tracks
# these three things, so they live here rather than being bolted onto
# an unrelated monitor).
# --------------------------------------------------------------------

class ProcessUptimeTracker:
    """Records when the current process started; `uptime_seconds()` is
    just `now - started_at`. A single float, but wrapped in a class so
    tests can construct an isolated instance instead of sharing the
    process-wide singleton's clock."""

    def __init__(self):
        self.started_at = time.time()

    def uptime_seconds(self) -> float:
        return time.time() - self.started_at


class PeakTracker:
    """Tracks the highest value ever seen per named gauge (e.g.
    "cpu_pct", "memory_pct"). Fed by calling `sample(name, value)` --
    typically once per resource_monitor.sample() call -- since
    resource_monitor.py itself only reports the *current* reading, not
    a running peak (see its own docstring: "purely additive", no
    history retained beyond the RollingWindow gauges some callers
    register separately, which is a different thing from a
    lifetime-for-this-process peak)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._peaks: Dict[str, float] = {}

    def sample(self, name: str, value: Optional[float]) -> None:
        if value is None:
            return
        with self._lock:
            current = self._peaks.get(name)
            if current is None or value > current:
                self._peaks[name] = value

    def peak(self, name: str) -> Optional[float]:
        with self._lock:
            return self._peaks.get(name)

    def all_peaks(self) -> Dict[str, float]:
        with self._lock:
            return dict(self._peaks)

    def reset(self) -> None:
        with self._lock:
            self._peaks.clear()


class AvailabilityTracker:
    """Records a HEALTHY/not-HEALTHY sample each time `record(state)`
    is called (typically once per health check) and reports the
    percentage of recorded samples that were HEALTHY. This is a
    sampled approximation of uptime, not a continuous measurement --
    accuracy depends on how often `record()` is called, same
    limitation any polling-based availability metric has (see
    live_service.py's own "poll-and-diff, not publish-on-write"
    design-decision note for the same tradeoff elsewhere in this
    project). Bounded history (deque via a plain list + max_samples
    trim) so this cannot grow unbounded in a long-running process."""

    def __init__(self, max_samples: int = 10000):
        self._lock = threading.Lock()
        self._samples: List[bool] = []
        self.max_samples = max_samples

    def record(self, state: HealthState) -> None:
        with self._lock:
            self._samples.append(state == HealthState.HEALTHY)
            if len(self._samples) > self.max_samples:
                self._samples = self._samples[-self.max_samples:]

    def availability_pct(self) -> Optional[float]:
        with self._lock:
            if not self._samples:
                return None
            return round(100.0 * sum(self._samples) / len(self._samples), 2)

    def sample_count(self) -> int:
        with self._lock:
            return len(self._samples)


# Process-wide default instances -- import these rather than
# constructing your own unless you need isolated state (e.g. per-test).
uptime_tracker = ProcessUptimeTracker()
peak_tracker = PeakTracker()
availability_tracker = AvailabilityTracker()


# --------------------------------------------------------------------
# Startup Report
# --------------------------------------------------------------------

@dataclass
class ComponentStartupStatus:
    name: str
    state: str
    message: str


@dataclass
class StartupDiagnostics:
    startup_validation: Optional[StartupReport]
    components: List[ComponentStartupStatus] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        no_critical_validation_failures = (
            self.startup_validation is None or not self.startup_validation.critical_failures
        )
        no_offline_components = all(c.state not in ("OFFLINE", "CRITICAL") for c in self.components)
        return no_critical_validation_failures and no_offline_components

    def render(self) -> str:
        lines = ["=" * 70, "STARTUP DIAGNOSTICS", "=" * 70]
        if self.startup_validation is not None:
            lines.append("Environment / Configuration / Dependencies:")
            for c in self.startup_validation.checks:
                lines.append(f"  [{c.status.value.upper():8s}] {c.name}: {c.message}")
        else:
            lines.append("Environment / Configuration / Dependencies: (not assessed)")
        lines.append("")
        lines.append("Components (Broker / Telegram / Scanner / Dashboard / Health):")
        for c in self.components:
            lines.append(f"  [{c.state:8s}] {c.name}: {c.message}")
        if not self.components:
            lines.append("  (none supplied)")
        lines.append("")
        lines.append(f"Overall: {'OK' if self.ok else 'ISSUES FOUND'}")
        lines.append("=" * 70)
        return "\n".join(lines)


def generate_startup_report(
    startup_validation: Optional[StartupReport] = None,
    broker_status: Optional[Callable[[], Any]] = None,
    telegram_status: Optional[Callable[[], Any]] = None,
    scanner_status: Optional[Callable[[], Any]] = None,
    dashboard_status: Optional[Callable[[], Any]] = None,
    health_status: Optional[Callable[[], Any]] = None,
) -> StartupDiagnostics:
    """Each `*_status` callable is expected to be a monitor's
    `.health()` method (returns a `ComponentHealth`) -- pass
    `broker_monitor.health`, `scanner_monitor.health`, etc. Omitted
    ones are simply not included in the component list, matching
    every other Part 9/10 report's "not assessed, not an error" rule.
    """
    components: List[ComponentStartupStatus] = []
    for name, getter in (
        ("broker", broker_status), ("telegram", telegram_status),
        ("scanner", scanner_status), ("dashboard", dashboard_status),
        ("health", health_status),
    ):
        if getter is None:
            continue
        try:
            result = getter()
            components.append(ComponentStartupStatus(name=name, state=str(result.state), message=result.message))
        except Exception as exc:  # noqa: BLE001 - a broken probe must not break the startup report
            components.append(ComponentStartupStatus(name=name, state="OFFLINE",
                                                       message=f"status check raised: {type(exc).__name__}: {exc}"))

    return StartupDiagnostics(startup_validation=startup_validation, components=components)


# --------------------------------------------------------------------
# Runtime Report
# --------------------------------------------------------------------

@dataclass
class RuntimeDiagnostics:
    uptime_seconds: float
    average_scan_time_seconds: Optional[float]
    average_api_time_ms: Optional[float]
    peak_cpu_pct: Optional[float]
    peak_memory_pct: Optional[float]
    alerts_today: int
    errors_today: int
    failures_today: int
    restarts_today: int
    worker_status: List[Dict[str, Any]]
    connection_status: Dict[str, Any]
    health_score: Optional[float]
    production_score: Optional[float]
    availability_pct: Optional[float]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "uptime_seconds": self.uptime_seconds,
            "average_scan_time_seconds": self.average_scan_time_seconds,
            "average_api_time_ms": self.average_api_time_ms,
            "peak_cpu_pct": self.peak_cpu_pct,
            "peak_memory_pct": self.peak_memory_pct,
            "alerts_today": self.alerts_today,
            "errors_today": self.errors_today,
            "failures_today": self.failures_today,
            "restarts_today": self.restarts_today,
            "worker_status": self.worker_status,
            "connection_status": self.connection_status,
            "health_score": self.health_score,
            "production_score": self.production_score,
            "availability_pct": self.availability_pct,
        }


def _health_score_from_registry(health_registry_obj) -> Optional[float]:
    """0-100 score derived from HealthRegistry.check_all() -- the same
    worst-of-N state health_registry.py already computes, mapped onto
    a number so a dashboard can chart it over time. Not a duplicate
    scoring system: HealthState itself has no numeric scale, so this
    is the one place that maps its 5 states to 0-100 for reporting."""
    if health_registry_obj is None:
        return None
    state_scores = {
        HealthState.HEALTHY: 100.0, HealthState.WARNING: 75.0,
        HealthState.DEGRADED: 50.0, HealthState.CRITICAL: 25.0, HealthState.OFFLINE: 0.0,
    }
    try:
        components = health_registry_obj.check_all()
    except Exception:  # noqa: BLE001
        return None
    if not components:
        return None
    scores = [state_scores[c.state] for c in components.values()]
    return round(sum(scores) / len(scores), 1)


def generate_runtime_report(
    scanner_monitor=None,
    api_monitor=None,
    resource_monitor=None,
    alert_engine=None,
    alert_sources=None,
    task_manager=None,
    broker_monitor=None,
    websocket_monitor=None,
    health_registry=None,
    production_report=None,
    uptime_tracker: ProcessUptimeTracker = uptime_tracker,
    peak_tracker: PeakTracker = peak_tracker,
    availability_tracker: AvailabilityTracker = availability_tracker,
) -> RuntimeDiagnostics:
    """Every argument optional; each contributes one piece of the
    report and is skipped (field left None/0/empty) if not supplied.
    Sampling side effects: if `resource_monitor` is given, this call
    also feeds `peak_tracker` (so peaks accumulate across repeated
    calls); if `health_registry` is given, this call also feeds
    `availability_tracker` with the current overall_state, so
    `availability_pct` reflects sampling frequency = call frequency."""
    average_scan_time = scanner_monitor.snapshot().average_scan_time_seconds if scanner_monitor is not None else None

    average_api_time_ms = None
    if api_monitor is not None:
        totals_endpoints = api_monitor.all_snapshots().values()
        times = [s.avg_response_time_ms for s in totals_endpoints if s.avg_response_time_ms is not None]
        if times:
            average_api_time_ms = round(sum(times) / len(times), 2)

    if resource_monitor is not None:
        snap = resource_monitor.sample()
        peak_tracker.sample("cpu_pct", snap.cpu_pct)
        peak_tracker.sample("memory_pct", snap.memory_pct)

    alerts_today = errors_today = failures_today = 0
    if alert_engine is not None and alert_sources is not None:
        alert_engine.evaluate(alert_sources)
        alerts_today = len(alert_engine.history())
        errors_today = sum(1 for a in alert_engine.history() if a.severity.name in ("ERROR", "CRITICAL"))
        failures_today = sum(1 for a in alert_engine.history() if "failed" in a.message or "failures" in a.message)

    restarts_today = 0
    worker_status: List[Dict[str, Any]] = []
    if task_manager is not None:
        statuses = task_manager.all_status()
        worker_status = [
            {"name": s.name, "state": s.state.value, "restart_count": s.restart_count,
             "heartbeat_age_seconds": s.heartbeat_age_seconds(), "last_error": s.last_error}
            for s in statuses
        ]
        restarts_today = sum(s.restart_count for s in statuses)

    connection_status: Dict[str, Any] = {}
    if broker_monitor is not None:
        connection_status["broker_connected"] = broker_monitor.snapshot().connected
    if websocket_monitor is not None:
        connection_status["websocket_active_clients"] = websocket_monitor.snapshot().active_clients

    health_score = None
    if health_registry is not None:
        health_score = _health_score_from_registry(health_registry)
        availability_tracker.record(health_registry.overall_state())

    production_score = production_report.readiness_percent if production_report is not None else None

    return RuntimeDiagnostics(
        uptime_seconds=round(uptime_tracker.uptime_seconds(), 1),
        average_scan_time_seconds=average_scan_time,
        average_api_time_ms=average_api_time_ms,
        peak_cpu_pct=peak_tracker.peak("cpu_pct"),
        peak_memory_pct=peak_tracker.peak("memory_pct"),
        alerts_today=alerts_today,
        errors_today=errors_today,
        failures_today=failures_today,
        restarts_today=restarts_today,
        worker_status=worker_status,
        connection_status=connection_status,
        health_score=health_score,
        production_score=production_score,
        availability_pct=availability_tracker.availability_pct(),
    )
