"""
production/resource_monitor.py
--------------------------------
Part 9, item 6: Resource Monitoring.

Continuously (or on-demand) samples memory, CPU, disk, thread count,
and any custom gauges the caller registers (API latency, queue size,
WebSocket connection count, background task count) and evaluates them
against warning/critical thresholds -- so a slow leak or a stuck queue
shows up as a WARNING log line well before it becomes an OOM kill or a
hung process.

Design notes
------------
- psutil is the only new third-party dependency this introduces, and
  it's optional: if it isn't installed (e.g. the ephemeral GitHub
  Actions scanner containers, which have no reason to carry a
  dashboard-only dependency), memory/CPU/disk sampling degrades to
  "unknown" (reported, not crashed) while thread count and any custom
  gauges still work.
- Queue size, WebSocket connection count, and background task count
  are NOT read directly from ws_manager.py / task_manager.py to avoid
  a hard import dependency in either direction -- callers register a
  zero-argument callable per gauge name via `register_gauge()`, and
  this module just calls it when sampling. This keeps resource_monitor
  usable standalone (e.g. inside the ephemeral scanner process, which
  has no WebSocket manager at all) and inside the dashboard process
  (which does).
- Purely additive: nothing existing has to call this for anything to
  keep working.
"""

from __future__ import annotations

import shutil
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional

try:
    import psutil
    _PSUTIL_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised in environments without psutil
    psutil = None  # type: ignore[assignment]
    _PSUTIL_AVAILABLE = False

from exceptions import ResourceLimitExceeded


class AlertLevel(Enum):
    OK = "ok"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(frozen=True)
class ResourceThresholds:
    """One knob per resource, warning below critical by construction
    (validated in __post_init__) so a warning always fires before the
    matching critical, never after."""

    memory_warning_pct: float = 75.0
    memory_critical_pct: float = 90.0
    cpu_warning_pct: float = 75.0
    cpu_critical_pct: float = 90.0
    disk_warning_pct: float = 80.0
    disk_critical_pct: float = 95.0
    thread_warning_count: int = 50
    thread_critical_count: int = 100
    # Generic thresholds applied to any custom gauge that doesn't have
    # its own named pair below (api_latency_ms, queue_size, etc. all
    # commonly registered via register_gauge()).
    gauge_warning_defaults: Dict[str, float] = field(default_factory=lambda: {
        "api_latency_ms": 1000.0,
        "queue_size": 500.0,
        "ws_connections": 200.0,
        "background_task_count": 30.0,
    })
    gauge_critical_defaults: Dict[str, float] = field(default_factory=lambda: {
        "api_latency_ms": 3000.0,
        "queue_size": 2000.0,
        "ws_connections": 500.0,
        "background_task_count": 60.0,
    })

    def __post_init__(self):
        pairs = [
            ("memory", self.memory_warning_pct, self.memory_critical_pct),
            ("cpu", self.cpu_warning_pct, self.cpu_critical_pct),
            ("disk", self.disk_warning_pct, self.disk_critical_pct),
            ("thread", self.thread_warning_count, self.thread_critical_count),
        ]
        for name, warn, crit in pairs:
            if warn > crit:
                raise ValueError(f"{name}: warning threshold ({warn}) must be <= critical ({crit}).")
        for gauge, warn in self.gauge_warning_defaults.items():
            crit = self.gauge_critical_defaults.get(gauge)
            if crit is not None and warn > crit:
                raise ValueError(f"gauge '{gauge}': warning ({warn}) must be <= critical ({crit}).")


@dataclass
class ResourceSnapshot:
    timestamp: float
    memory_pct: Optional[float]
    cpu_pct: Optional[float]
    disk_pct: Optional[float]
    thread_count: int
    gauges: Dict[str, float] = field(default_factory=dict)


@dataclass
class ResourceAlert:
    resource: str
    level: AlertLevel
    value: float
    threshold: float
    message: str


class ResourceMonitor:
    """Not a background thread by itself (item 8's task manager owns
    scheduling) -- call `sample_and_evaluate()` periodically (e.g. once
    per scan cycle, or from a background task) and act on the
    returned alerts."""

    def __init__(self, thresholds: Optional[ResourceThresholds] = None,
                 disk_path: str = "."):
        self.thresholds = thresholds or ResourceThresholds()
        self.disk_path = disk_path
        self._gauges: Dict[str, Callable[[], float]] = {}
        self._lock = threading.Lock()

    def register_gauge(self, name: str, getter: Callable[[], float]) -> None:
        """Register a zero-arg callable returning a current metric
        value, e.g. register_gauge("queue_size", lambda: my_queue.qsize())."""
        with self._lock:
            self._gauges[name] = getter

    def unregister_gauge(self, name: str) -> None:
        with self._lock:
            self._gauges.pop(name, None)

    def sample(self) -> ResourceSnapshot:
        memory_pct = cpu_pct = disk_pct = None
        if _PSUTIL_AVAILABLE:
            try:
                memory_pct = psutil.virtual_memory().percent
            except Exception:  # noqa: BLE001 - never let a sampling failure crash the caller
                memory_pct = None
            try:
                cpu_pct = psutil.cpu_percent(interval=None)
            except Exception:  # noqa: BLE001
                cpu_pct = None

        try:
            usage = shutil.disk_usage(self.disk_path)
            disk_pct = (usage.used / usage.total) * 100 if usage.total else None
        except OSError:
            disk_pct = None

        gauges: Dict[str, float] = {}
        with self._lock:
            getters = dict(self._gauges)
        for name, getter in getters.items():
            try:
                gauges[name] = float(getter())
            except Exception:  # noqa: BLE001 - one broken gauge must not break sampling
                continue

        return ResourceSnapshot(
            timestamp=time.time(),
            memory_pct=memory_pct,
            cpu_pct=cpu_pct,
            disk_pct=disk_pct,
            thread_count=threading.active_count(),
            gauges=gauges,
        )

    def evaluate(self, snapshot: ResourceSnapshot) -> List[ResourceAlert]:
        alerts: List[ResourceAlert] = []
        t = self.thresholds

        def _check(resource: str, value: Optional[float], warn: float, crit: float) -> None:
            if value is None:
                return
            if value >= crit:
                alerts.append(ResourceAlert(
                    resource, AlertLevel.CRITICAL, value, crit,
                    f"{resource} at {value:.1f} >= critical threshold {crit:.1f}.",
                ))
            elif value >= warn:
                alerts.append(ResourceAlert(
                    resource, AlertLevel.WARNING, value, warn,
                    f"{resource} at {value:.1f} >= warning threshold {warn:.1f}.",
                ))

        _check("memory_pct", snapshot.memory_pct, t.memory_warning_pct, t.memory_critical_pct)
        _check("cpu_pct", snapshot.cpu_pct, t.cpu_warning_pct, t.cpu_critical_pct)
        _check("disk_pct", snapshot.disk_pct, t.disk_warning_pct, t.disk_critical_pct)
        _check("thread_count", float(snapshot.thread_count), t.thread_warning_count, t.thread_critical_count)

        for name, value in snapshot.gauges.items():
            warn = t.gauge_warning_defaults.get(name)
            crit = t.gauge_critical_defaults.get(name)
            if warn is not None and crit is not None:
                _check(name, value, warn, crit)

        return alerts

    def sample_and_evaluate(self) -> tuple[ResourceSnapshot, List[ResourceAlert]]:
        snapshot = self.sample()
        return snapshot, self.evaluate(snapshot)

    def raise_if_critical(self, alerts: List[ResourceAlert]) -> None:
        """Optional strict mode for callers that want a hard stop
        (e.g. refuse to start a new scan cycle) rather than just
        logging -- most callers should just log the alerts instead."""
        critical = [a for a in alerts if a.level is AlertLevel.CRITICAL]
        if critical:
            worst = critical[0]
            raise ResourceLimitExceeded(
                worst.message, resource=worst.resource, value=worst.value, threshold=worst.threshold,
            )


# Process-wide default instance -- import this rather than constructing
# your own unless you need isolated thresholds/gauges (e.g. per-test).
resource_monitor = ResourceMonitor()
