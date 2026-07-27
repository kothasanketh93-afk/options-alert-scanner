"""
production/telegram_monitor.py
---------------------------------
Part 10, item 5: Telegram Monitoring.

Tracks connection status, messages sent, failures, retry count, and
delivery latency -- fed by telegram_notify.py calling
record_sent()/record_failure()/record_retry() around its existing
send calls. Same decoupled-observer pattern as scanner_monitor.py /
broker_monitor.py; this module does not import telegram_notify.py.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Optional

from production.metrics_utils import RollingWindow
from production.health_states import ComponentHealth, HealthState


@dataclass
class TelegramSnapshot:
    connected: bool
    messages_sent: int
    failures: int
    retry_count: int
    delivery_latency_ms_avg: Optional[float]
    delivery_latency_ms_p95: Optional[float]


class TelegramMonitor:
    def __init__(self):
        self._lock = threading.Lock()
        self._delivery_latency = RollingWindow(max_samples=200)
        self._connected = True  # Telegram has no explicit "connect" step -- assumed reachable until proven otherwise
        self._messages_sent = 0
        self._failures = 0
        self._retry_count = 0

    def record_sent(self, latency_ms: float) -> None:
        with self._lock:
            self._messages_sent += 1
            self._connected = True
            self._delivery_latency.record(latency_ms)

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1

    def record_retry(self) -> None:
        with self._lock:
            self._retry_count += 1

    def record_unreachable(self) -> None:
        """Distinct from record_failure(): a failure is one bad send
        (rejected message, transient error); unreachable means
        Telegram's API itself couldn't be reached at all (DNS/network
        failure), which is what should actually flip `connected` to
        False for health reporting."""
        with self._lock:
            self._connected = False
            self._failures += 1

    def snapshot(self) -> TelegramSnapshot:
        with self._lock:
            stats = self._delivery_latency.stats()
            return TelegramSnapshot(
                connected=self._connected,
                messages_sent=self._messages_sent,
                failures=self._failures,
                retry_count=self._retry_count,
                delivery_latency_ms_avg=stats.average,
                delivery_latency_ms_p95=stats.p95,
            )

    def health(self) -> ComponentHealth:
        snap = self.snapshot()
        state = HealthState.HEALTHY
        reasons = []

        total = snap.messages_sent + snap.failures
        if not snap.connected:
            state = HealthState.OFFLINE
            reasons.append("Telegram API unreachable")
        elif total > 0 and (snap.failures / total) > 0.3:
            state = HealthState.DEGRADED
            reasons.append(f"{snap.failures}/{total} recent messages failed")
        elif snap.retry_count > 5:
            state = HealthState.WARNING
            reasons.append(f"{snap.retry_count} retries so far")

        message = "; ".join(reasons) if reasons else "telegram nominal"
        return ComponentHealth(
            name="telegram", state=state, message=message,
            metrics={
                "connected": snap.connected, "messages_sent": snap.messages_sent,
                "failures": snap.failures, "retry_count": snap.retry_count,
                "delivery_latency_ms_avg": snap.delivery_latency_ms_avg,
                "delivery_latency_ms_p95": snap.delivery_latency_ms_p95,
            },
        )


# Process-wide default instance.
telegram_monitor = TelegramMonitor()
