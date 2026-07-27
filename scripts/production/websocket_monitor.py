"""
production/websocket_monitor.py
----------------------------------
Part 10, Phase 3: WebSocket Monitoring.

Tracks connected/active/disconnected clients, reconnect attempts and
successes, broadcast counts/failures/timing, dropped messages,
connection durations, ping/latency, last-broadcast time, and current
subscriber count -- fed by app/backend/api/ws_manager.py calling
record_connect()/record_disconnect()/record_broadcast()/etc. around
its existing connect/disconnect/broadcast methods.

Same decoupled-observer pattern as scanner_monitor.py / broker_monitor.py
/ telegram_monitor.py: this module does not import ws_manager.py, does
not touch FastAPI/Starlette, and has no opinion on *why* a client
disconnected -- ws_manager.py is still the only thing that owns the
actual socket set.

"Active clients" vs "connected clients": connected_clients is a
lifetime counter (every accept() ever seen); active_clients is the
current open-connection count (incremented on connect, decremented on
disconnect) -- so a dashboard can show both "how many have ever
connected this process" and "how many are open right now" without
this module needing to know the live socket set itself (ws_manager.py
already tracks that; active_clients here is a mirror the caller keeps
in sync via record_connect()/record_disconnect()).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Dict, Optional

from production.metrics_utils import RollingWindow
from production.health_states import ComponentHealth, HealthState

# A broadcast slower than this is counted as "slow" for health purposes.
DEFAULT_SLOW_BROADCAST_MS = 500.0
# No successful broadcast within this many seconds (while clients are
# connected) is treated as suspicious -- the live feed may have stalled.
DEFAULT_STALE_BROADCAST_SECONDS = 300.0


@dataclass
class WebSocketSnapshot:
    connected_clients: int          # lifetime total accepted
    active_clients: int             # currently open
    disconnected_clients: int       # lifetime total closed
    reconnect_count: int
    reconnect_success: int
    broadcast_count: int
    broadcast_failures: int
    avg_broadcast_time_ms: Optional[float]
    max_broadcast_time_ms: Optional[float]
    dropped_messages: int
    avg_connection_duration_seconds: Optional[float]
    last_ping_at: Optional[float]
    avg_latency_ms: Optional[float]
    last_broadcast_at: Optional[float]
    current_subscribers: int


class WebSocketMonitor:
    def __init__(self, slow_broadcast_ms: float = DEFAULT_SLOW_BROADCAST_MS,
                 stale_broadcast_seconds: float = DEFAULT_STALE_BROADCAST_SECONDS):
        self._lock = threading.Lock()
        self._broadcast_times = RollingWindow(max_samples=300)
        self._connection_durations = RollingWindow(max_samples=300)
        self._latency = RollingWindow(max_samples=300)
        self._connect_started_at: Dict[int, float] = {}  # id(ws) -> connected-at epoch

        self._connected_clients = 0
        self._active_clients = 0
        self._disconnected_clients = 0
        self._reconnect_count = 0
        self._reconnect_success = 0
        self._broadcast_count = 0
        self._broadcast_failures = 0
        self._dropped_messages = 0
        self._last_ping_at: Optional[float] = None
        self._last_broadcast_at: Optional[float] = None
        self._current_subscribers = 0

        self.slow_broadcast_ms = slow_broadcast_ms
        self.stale_broadcast_seconds = stale_broadcast_seconds

    # -- connection lifecycle -------------------------------------------------
    def record_connect(self, client_key: Optional[int] = None) -> None:
        with self._lock:
            self._connected_clients += 1
            self._active_clients += 1
            self._current_subscribers = self._active_clients
            if client_key is not None:
                self._connect_started_at[client_key] = time.time()

    def record_disconnect(self, client_key: Optional[int] = None) -> None:
        with self._lock:
            self._disconnected_clients += 1
            self._active_clients = max(0, self._active_clients - 1)
            self._current_subscribers = self._active_clients
            started = self._connect_started_at.pop(client_key, None) if client_key is not None else None
            if started is not None:
                self._connection_durations.record(time.time() - started)

    def record_reconnect_attempt(self, success: bool) -> None:
        with self._lock:
            self._reconnect_count += 1
            if success:
                self._reconnect_success += 1

    # -- broadcasts -------------------------------------------------------------
    def record_broadcast(self, duration_ms: float, recipients: int,
                          failures: int = 0, dropped: int = 0) -> None:
        with self._lock:
            now = time.time()
            self._broadcast_count += 1
            self._broadcast_times.record(duration_ms, at=now)
            self._broadcast_failures += failures
            self._dropped_messages += dropped
            self._last_broadcast_at = now

    def record_ping(self, latency_ms: Optional[float] = None) -> None:
        with self._lock:
            self._last_ping_at = time.time()
            if latency_ms is not None:
                self._latency.record(latency_ms)

    def snapshot(self) -> WebSocketSnapshot:
        with self._lock:
            b_stats = self._broadcast_times.stats()
            d_stats = self._connection_durations.stats()
            l_stats = self._latency.stats()
            return WebSocketSnapshot(
                connected_clients=self._connected_clients,
                active_clients=self._active_clients,
                disconnected_clients=self._disconnected_clients,
                reconnect_count=self._reconnect_count,
                reconnect_success=self._reconnect_success,
                broadcast_count=self._broadcast_count,
                broadcast_failures=self._broadcast_failures,
                avg_broadcast_time_ms=b_stats.average,
                max_broadcast_time_ms=b_stats.maximum,
                dropped_messages=self._dropped_messages,
                avg_connection_duration_seconds=d_stats.average,
                last_ping_at=self._last_ping_at,
                avg_latency_ms=l_stats.average,
                last_broadcast_at=self._last_broadcast_at,
                current_subscribers=self._current_subscribers,
            )

    def health(self) -> ComponentHealth:
        snap = self.snapshot()
        now = time.time()
        state = HealthState.HEALTHY
        reasons = []

        if snap.broadcast_count > 0 and (snap.broadcast_failures / snap.broadcast_count) > 0.2:
            state = HealthState.DEGRADED
            reasons.append(f"{snap.broadcast_failures}/{snap.broadcast_count} recent broadcasts failed")
        if snap.avg_broadcast_time_ms is not None and snap.avg_broadcast_time_ms > self.slow_broadcast_ms:
            state = max(state, HealthState.WARNING)
            reasons.append(f"average broadcast time {snap.avg_broadcast_time_ms:.0f}ms exceeds {self.slow_broadcast_ms:.0f}ms")
        if (snap.active_clients > 0 and snap.last_broadcast_at is not None
                and (now - snap.last_broadcast_at) > self.stale_broadcast_seconds):
            state = max(state, HealthState.WARNING)
            reasons.append(f"no broadcast in over {self.stale_broadcast_seconds:.0f}s while clients are connected")
        if snap.reconnect_count > 0 and (snap.reconnect_success / snap.reconnect_count) < 0.5:
            state = max(state, HealthState.DEGRADED)
            reasons.append(f"only {snap.reconnect_success}/{snap.reconnect_count} reconnect attempts succeeded")

        message = "; ".join(reasons) if reasons else "websocket nominal"
        return ComponentHealth(
            name="websocket", state=state, message=message,
            metrics={
                "connected_clients": snap.connected_clients, "active_clients": snap.active_clients,
                "disconnected_clients": snap.disconnected_clients, "reconnect_count": snap.reconnect_count,
                "reconnect_success": snap.reconnect_success, "broadcast_count": snap.broadcast_count,
                "broadcast_failures": snap.broadcast_failures, "avg_broadcast_time_ms": snap.avg_broadcast_time_ms,
                "max_broadcast_time_ms": snap.max_broadcast_time_ms, "dropped_messages": snap.dropped_messages,
                "avg_connection_duration_seconds": snap.avg_connection_duration_seconds,
                "last_ping_at": snap.last_ping_at, "avg_latency_ms": snap.avg_latency_ms,
                "last_broadcast_at": snap.last_broadcast_at, "current_subscribers": snap.current_subscribers,
            },
        )

    def status(self) -> Dict[str, object]:
        """Shape expected by production.health_aggregator.HealthProviders.websocket_status
        (a plain dict, not a ComponentHealth) -- see that module's docstring."""
        snap = self.snapshot()
        return {
            "active_clients": snap.active_clients,
            "broadcast_count": snap.broadcast_count,
            "broadcast_failures": snap.broadcast_failures,
            "last_broadcast_at": snap.last_broadcast_at,
        }


# Process-wide default instance.
websocket_monitor = WebSocketMonitor()
