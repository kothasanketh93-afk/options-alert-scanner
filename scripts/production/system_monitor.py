"""
production/system_monitor.py
-------------------------------
Part 10, item 2: System Monitoring.

Extends (via composition, not modification) Part 9's
production.resource_monitor.ResourceMonitor with the metrics Part 10
additionally asks for that Part 9 didn't need: open file count,
running task count, queue length, API latency, response time, and
per-worker status -- then classifies the result into a single
HealthState (production.health_states) instead of Part 9's
warning/critical alert list, since Part 10's Health Monitoring (item
1) and Dashboard Monitoring (item 8) both want one state per
component, not a raw alert list.

Relationship to production.resource_monitor
---------------------------------------------
resource_monitor.py is NOT modified. SystemMonitor here *uses* a
ResourceMonitor instance for CPU/memory/disk/thread-count/gauge
sampling (the exact same sampling code, no duplication) and adds:
  - open file count (psutil, optional, same graceful-degrade pattern)
  - running_tasks / worker_status, pulled from a
    production.task_manager.TaskManager if one is supplied
  - queue_length / api_latency_ms / response_time_ms as gauges, same
    register_gauge() mechanism resource_monitor.py already has --
    reused here rather than re-invented.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from production.resource_monitor import ResourceMonitor
from production.task_manager import TaskManager, WorkerState
from production.health_states import ComponentHealth, HealthState, combine, state_from_pct

try:
    import psutil
    _PSUTIL_AVAILABLE = True
except ImportError:  # pragma: no cover
    psutil = None  # type: ignore[assignment]
    _PSUTIL_AVAILABLE = False


@dataclass
class SystemSnapshot:
    cpu_pct: Optional[float]
    memory_pct: Optional[float]
    disk_pct: Optional[float]
    open_files: Optional[int]
    thread_count: int
    running_tasks: int
    queue_length: Optional[float]
    api_latency_ms: Optional[float]
    response_time_ms: Optional[float]
    worker_status: Dict[str, str] = field(default_factory=dict)


def _open_file_count() -> Optional[int]:
    if not _PSUTIL_AVAILABLE:
        return None
    try:
        return len(psutil.Process().open_files())
    except Exception:  # noqa: BLE001 - permissions/platform quirks must not crash monitoring
        return None


class SystemMonitor:
    """Wraps a ResourceMonitor (Part 9) + optional TaskManager (Part
    9) to produce one ComponentHealth for "system" -- CPU/memory/disk/
    open-files/threads via psutil, running tasks + per-worker status
    via TaskManager, queue length/API latency/response time via the
    same register_gauge() mechanism resource_monitor already exposes.
    """

    def __init__(self, resource_monitor: Optional[ResourceMonitor] = None,
                 task_manager: Optional[TaskManager] = None):
        self.resource_monitor = resource_monitor or ResourceMonitor()
        self.task_manager = task_manager

    def sample(self) -> SystemSnapshot:
        snapshot = self.resource_monitor.sample()
        worker_status: Dict[str, str] = {}
        running_tasks = 0
        if self.task_manager is not None:
            running_tasks = self.task_manager.running_count
            worker_status = {s.name: s.state.value for s in self.task_manager.all_status()}

        return SystemSnapshot(
            cpu_pct=snapshot.cpu_pct,
            memory_pct=snapshot.memory_pct,
            disk_pct=snapshot.disk_pct,
            open_files=_open_file_count(),
            thread_count=snapshot.thread_count,
            running_tasks=running_tasks,
            queue_length=snapshot.gauges.get("queue_size"),
            api_latency_ms=snapshot.gauges.get("api_latency_ms"),
            response_time_ms=snapshot.gauges.get("response_time_ms"),
            worker_status=worker_status,
        )

    def health(self) -> ComponentHealth:
        snap = self.sample()
        states = [
            state_from_pct(snap.cpu_pct),
            state_from_pct(snap.memory_pct),
            state_from_pct(snap.disk_pct),
        ]
        if self.task_manager is not None:
            crashed = [n for n, s in snap.worker_status.items() if s == WorkerState.CRASHED.value]
            if crashed:
                states.append(HealthState.DEGRADED)
            if self.task_manager.check_health():  # missed heartbeat -> possibly hung
                states.append(HealthState.WARNING)

        overall = combine(states)
        message = "system nominal" if overall == HealthState.HEALTHY else "see metrics for degraded component(s)"
        return ComponentHealth(
            name="system", state=overall, message=message,
            metrics={
                "cpu_pct": snap.cpu_pct, "memory_pct": snap.memory_pct, "disk_pct": snap.disk_pct,
                "open_files": snap.open_files, "thread_count": snap.thread_count,
                "running_tasks": snap.running_tasks, "queue_length": snap.queue_length,
                "api_latency_ms": snap.api_latency_ms, "response_time_ms": snap.response_time_ms,
                "worker_status": snap.worker_status,
            },
        )
