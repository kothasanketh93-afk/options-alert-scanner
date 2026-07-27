"""
production/alert_engine.py
-----------------------------
Part 10, Phase 4: Alerting Engine.

Turns the numbers every Part 9/10 monitor already collects (resource_
monitor, broker_monitor, telegram_monitor, scanner_monitor, api_monitor,
websocket_monitor, task_manager) into a stream of Alert records with a
severity (INFO/WARNING/ERROR/CRITICAL), plus the three things a raw
threshold check doesn't give you for free:

  - cooldown     -- the same underlying problem does not re-fire a new
                     alert every single evaluation cycle; it re-fires
                     only after `cooldown_seconds` has passed since it
                     last fired (or if it clears and re-occurs).
  - suppression  -- a rule can be muted for a time window (e.g. during
                     planned maintenance) without touching its
                     threshold or unregistering it.
  - aggregation  -- repeated firings of the same rule within its
                     cooldown window are folded into one history entry
                     with an occurrence count, instead of flooding
                     history with duplicates.

Same decoupled pattern as health_aggregator.py: this module does NOT
hard-import scanner.py, broker.py, main.py, or any trading logic --
only the existing production/* monitors (already decoupled from
trading logic themselves) and, optionally, a task_manager.TaskManager
instance (pass production.task_manager.task_manager, or a per-test
instance, via AlertSources if worker-failure alerts are wanted).

Wiring: call `alert_engine.evaluate()` periodically (e.g. once per
scan cycle from main.py, or from a production.task_manager background
worker in the dashboard process -- see MIGRATION_NOTES.md). Nothing
calls this automatically.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Callable, Dict, List, Optional

from production.health_states import HealthState
from production.resource_monitor import ResourceMonitor, AlertLevel as ResourceAlertLevel


class AlertSeverity(IntEnum):
    """Ordered worst-to-best like HealthState, for the same reason:
    `max()` picks the worst severity with no lookup table."""
    INFO = 0
    WARNING = 1
    ERROR = 2
    CRITICAL = 3

    def __str__(self) -> str:
        return self.name


@dataclass
class Alert:
    rule: str
    severity: AlertSeverity
    message: str
    fired_at: float = field(default_factory=time.time)
    occurrences: int = 1  # aggregated count within the current cooldown window
    metrics: Dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, object]:
        return {
            "rule": self.rule,
            "severity": str(self.severity),
            "message": self.message,
            "fired_at": self.fired_at,
            "occurrences": self.occurrences,
            "metrics": self.metrics,
        }


@dataclass
class AlertSources:
    """Every field optional -- an unset source simply contributes no
    alerts for its rules, same "opt-in, no error" pattern as
    health_aggregator.HealthProviders. Pass the real module-level
    monitor instances (production.resource_monitor.resource_monitor,
    production.broker_monitor.broker_monitor, etc.) or per-test fakes."""
    resource_monitor: Optional[ResourceMonitor] = None
    broker_status: Optional[Callable[[], object]] = None       # -> ComponentHealth (broker_monitor.health)
    telegram_status: Optional[Callable[[], object]] = None     # -> ComponentHealth (telegram_monitor.health)
    scanner_status: Optional[Callable[[], object]] = None      # -> ComponentHealth (scanner_monitor.health)
    api_status: Optional[Callable[[], object]] = None          # -> ComponentHealth (api_monitor.health)
    websocket_status: Optional[Callable[[], object]] = None    # -> ComponentHealth (websocket_monitor.health)
    task_manager: Optional[object] = None                       # production.task_manager.TaskManager instance
    exception_counts: Optional[Callable[[], Dict[str, int]]] = None  # component -> consecutive failure count

    # thresholds a rule can't get from the underlying monitor's own
    # health() call (which only exposes HealthState, not a raw number)
    api_latency_warning_ms: float = 1000.0
    api_latency_critical_ms: float = 3000.0
    queue_size_warning: float = 500.0
    queue_size_critical: float = 2000.0
    repeated_exception_threshold: int = 3


_HEALTHSTATE_TO_SEVERITY = {
    HealthState.HEALTHY: None,
    HealthState.WARNING: AlertSeverity.WARNING,
    HealthState.DEGRADED: AlertSeverity.ERROR,
    HealthState.CRITICAL: AlertSeverity.CRITICAL,
    HealthState.OFFLINE: AlertSeverity.CRITICAL,
}

_RESOURCE_LEVEL_TO_SEVERITY = {
    ResourceAlertLevel.OK: None,
    ResourceAlertLevel.WARNING: AlertSeverity.WARNING,
    ResourceAlertLevel.CRITICAL: AlertSeverity.CRITICAL,
}


class AlertEngine:
    def __init__(self, cooldown_seconds: float = 300.0, history_limit: int = 500):
        self._lock = threading.Lock()
        self.cooldown_seconds = cooldown_seconds
        self._history_limit = history_limit
        self._history: List[Alert] = []
        self._last_fired: Dict[str, Alert] = {}  # rule -> most recent Alert (for cooldown/aggregation)
        self._suppressed_until: Dict[str, float] = {}  # rule -> epoch time suppression ends

    # -- suppression ------------------------------------------------------------
    def suppress(self, rule: str, seconds: float) -> None:
        with self._lock:
            self._suppressed_until[rule] = time.time() + seconds

    def unsuppress(self, rule: str) -> None:
        with self._lock:
            self._suppressed_until.pop(rule, None)

    def _is_suppressed(self, rule: str) -> bool:
        until = self._suppressed_until.get(rule)
        return until is not None and time.time() < until

    # -- core firing / cooldown / aggregation ------------------------------------
    def _fire(self, rule: str, severity: AlertSeverity, message: str,
               metrics: Optional[Dict[str, object]] = None) -> Optional[Alert]:
        now = time.time()
        with self._lock:
            if self._is_suppressed(rule):
                return None

            previous = self._last_fired.get(rule)
            if previous is not None and (now - previous.fired_at) < self.cooldown_seconds:
                # Aggregate: same rule re-firing within its cooldown
                # window updates the existing history entry's count
                # and latest message/metrics rather than adding a new
                # one, so a flapping check doesn't flood history.
                previous.occurrences += 1
                previous.message = message
                previous.metrics = metrics or {}
                previous.severity = max(previous.severity, severity)
                return previous

            alert = Alert(rule=rule, severity=severity, message=message, metrics=metrics or {})
            self._last_fired[rule] = alert
            self._history.append(alert)
            if len(self._history) > self._history_limit:
                self._history = self._history[-self._history_limit:]
            return alert

    def clear(self, rule: str) -> None:
        """Call when a rule's underlying condition has resolved, so
        the next occurrence is treated as a fresh alert rather than
        aggregated into a stale one from before it cleared."""
        with self._lock:
            self._last_fired.pop(rule, None)

    def history(self, severity_at_least: Optional[AlertSeverity] = None) -> List[Alert]:
        with self._lock:
            items = list(self._history)
        if severity_at_least is not None:
            items = [a for a in items if a.severity >= severity_at_least]
        return items

    def active(self) -> List[Alert]:
        """Alerts that fired within the current cooldown window --
        i.e. "still relevant right now" as opposed to full history."""
        now = time.time()
        with self._lock:
            return [a for a in self._last_fired.values() if (now - a.fired_at) < self.cooldown_seconds]

    def cleanup_history(self, max_age_seconds: float) -> int:
        """Part 10 Phase 6 'old alert cleanup': drops history entries
        older than `max_age_seconds`. Also drops any matching entry
        from `_last_fired` so a long-resolved rule doesn't keep
        aggregating into a stale alert forever. Returns the number of
        history entries dropped. Safe to call on a schedule (e.g. from
        a production.task_manager background worker); history_limit
        already bounds worst-case size, this is for time-based
        pruning on top of that count-based bound."""
        cutoff = time.time() - max_age_seconds
        with self._lock:
            before = len(self._history)
            self._history = [a for a in self._history if a.fired_at >= cutoff]
            dropped = before - len(self._history)
            stale_rules = [rule for rule, a in self._last_fired.items() if a.fired_at < cutoff]
            for rule in stale_rules:
                self._last_fired.pop(rule, None)
        return dropped

    # -- evaluation ---------------------------------------------------------------
    def evaluate(self, sources: AlertSources) -> List[Alert]:
        """Runs every rule once against the supplied sources and
        returns the alerts that fired *this call* (new or aggregated
        -- not full history). A source left unset in `sources`
        contributes nothing; a source whose own status call raises is
        skipped for that rule rather than aborting the rest (same
        never-let-one-check-break-the-others rule as health_registry.py)."""
        fired: List[Alert] = []

        def _safe(fn):
            if fn is None:
                return None
            try:
                return fn()
            except Exception:  # noqa: BLE001 - a broken probe must not break the rest of evaluation
                return None

        # -- CPU / Memory / Disk / queue overflow (resource_monitor) --------
        if sources.resource_monitor is not None:
            try:
                _, alerts = sources.resource_monitor.sample_and_evaluate()
            except Exception:  # noqa: BLE001
                alerts = []
            for a in alerts:
                severity = _RESOURCE_LEVEL_TO_SEVERITY.get(a.level)
                if severity is None:
                    continue
                rule = f"resource.{a.resource}"
                result = self._fire(rule, severity, a.message, metrics={"value": a.value, "threshold": a.threshold})
                if result is not None:
                    fired.append(result)

        # -- Broker disconnected --------------------------------------------
        health = _safe(sources.broker_status)
        if health is not None and _HEALTHSTATE_TO_SEVERITY.get(health.state) is not None:
            result = self._fire("broker.disconnected", _HEALTHSTATE_TO_SEVERITY[health.state],
                                 health.message, metrics=health.metrics)
            if result is not None:
                fired.append(result)

        # -- Telegram unavailable --------------------------------------------
        health = _safe(sources.telegram_status)
        if health is not None and _HEALTHSTATE_TO_SEVERITY.get(health.state) is not None:
            result = self._fire("telegram.unavailable", _HEALTHSTATE_TO_SEVERITY[health.state],
                                 health.message, metrics=health.metrics)
            if result is not None:
                fired.append(result)

        # -- Scanner inactive / no scans --------------------------------------
        health = _safe(sources.scanner_status)
        if health is not None and _HEALTHSTATE_TO_SEVERITY.get(health.state) is not None:
            result = self._fire("scanner.inactive", _HEALTHSTATE_TO_SEVERITY[health.state],
                                 health.message, metrics=health.metrics)
            if result is not None:
                fired.append(result)

        # -- API latency high --------------------------------------------------
        health = _safe(sources.api_status)
        if health is not None:
            # api_monitor.health()'s totals dict has no latency field (only
            # request/failure counts) -- this rule instead reads
            # api_monitor.all_snapshots() directly when available via
            # the same callable's __self__, falling back to skipping
            # the latency check if that's not possible (e.g. a fake in
            # tests that only implements .health()).
            monitor_obj = getattr(sources.api_status, "__self__", None)
            if monitor_obj is not None and hasattr(monitor_obj, "all_snapshots"):
                snapshots = monitor_obj.all_snapshots()
                worst = max(
                    (s.avg_response_time_ms for s in snapshots.values() if s.avg_response_time_ms is not None),
                    default=None,
                )
                if worst is not None:
                    if worst >= sources.api_latency_critical_ms:
                        result = self._fire("api.latency_high", AlertSeverity.CRITICAL,
                                             f"average endpoint latency {worst:.0f}ms >= {sources.api_latency_critical_ms:.0f}ms",
                                             metrics={"avg_latency_ms": worst})
                        if result is not None:
                            fired.append(result)
                    elif worst >= sources.api_latency_warning_ms:
                        result = self._fire("api.latency_high", AlertSeverity.WARNING,
                                             f"average endpoint latency {worst:.0f}ms >= {sources.api_latency_warning_ms:.0f}ms",
                                             metrics={"avg_latency_ms": worst})
                        if result is not None:
                            fired.append(result)

        # -- WebSocket failures --------------------------------------------------
        health = _safe(sources.websocket_status)
        if health is not None and _HEALTHSTATE_TO_SEVERITY.get(health.state) is not None:
            result = self._fire("websocket.failures", _HEALTHSTATE_TO_SEVERITY[health.state],
                                 health.message, metrics=health.metrics)
            if result is not None:
                fired.append(result)

        # -- Worker failures --------------------------------------------------
        if sources.task_manager is not None:
            try:
                unhealthy = sources.task_manager.check_health()
            except Exception:  # noqa: BLE001
                unhealthy = []
            for status in unhealthy:
                result = self._fire(
                    f"worker.{status.name}.unhealthy", AlertSeverity.ERROR,
                    f"worker '{status.name}' is unhealthy (state={status.state.value}, "
                    f"restarts={status.restart_count})",
                    metrics={"state": status.state.value, "restart_count": status.restart_count},
                )
                if result is not None:
                    fired.append(result)

        # -- Repeated exceptions --------------------------------------------------
        counts = _safe(sources.exception_counts)
        if counts:
            for component, count in counts.items():
                if count >= sources.repeated_exception_threshold:
                    result = self._fire(
                        f"exceptions.{component}.repeated", AlertSeverity.ERROR,
                        f"'{component}' has failed {count} consecutive times",
                        metrics={"consecutive_failures": count},
                    )
                    if result is not None:
                        fired.append(result)
                else:
                    self.clear(f"exceptions.{component}.repeated")

        return fired


# Process-wide default instance.
alert_engine = AlertEngine()
