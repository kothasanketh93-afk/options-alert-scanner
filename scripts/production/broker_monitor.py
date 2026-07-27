"""
production/broker_monitor.py
-------------------------------
Part 10, item 4: Broker Monitoring.

Tracks connection state, token expiry, reconnect attempts, API
failures, API latency, market-data latency, and rate-limit status --
by recording events from wherever broker.py / upstox.py / angelone.py
already know about them (a token refresh, an API call's latency, a
429 response). This module does not import broker.py, upstox.py, or
angelone.py -- same decoupled-observer pattern as scanner_monitor.py;
call record_api_call()/set_token_expiry()/etc. from those modules (or
from main.py around calls into them) to feed this.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Optional

from production.metrics_utils import RollingWindow
from production.health_states import ComponentHealth, HealthState

# Warn once token expiry is under this many seconds away.
DEFAULT_TOKEN_EXPIRY_WARNING_SECONDS = 600.0


@dataclass
class BrokerSnapshot:
    connected: bool
    token_expires_at: Optional[float]
    reconnect_attempts: int
    api_failures: int
    api_calls: int
    api_latency_ms_avg: Optional[float]
    api_latency_ms_p95: Optional[float]
    market_data_latency_ms_avg: Optional[float]
    rate_limited: bool
    rate_limit_remaining: Optional[int]


class BrokerMonitor:
    def __init__(self, token_expiry_warning_seconds: float = DEFAULT_TOKEN_EXPIRY_WARNING_SECONDS):
        self._lock = threading.Lock()
        self._api_latency = RollingWindow(max_samples=200)
        self._market_data_latency = RollingWindow(max_samples=200)
        self._connected = False
        self._token_expires_at: Optional[float] = None
        self._reconnect_attempts = 0
        self._api_failures = 0
        self._api_calls = 0
        self._rate_limited = False
        self._rate_limit_remaining: Optional[int] = None
        self.token_expiry_warning_seconds = token_expiry_warning_seconds

    def record_connect(self) -> None:
        with self._lock:
            self._connected = True

    def record_disconnect(self) -> None:
        with self._lock:
            self._connected = False

    def record_reconnect_attempt(self) -> None:
        with self._lock:
            self._reconnect_attempts += 1

    def record_api_call(self, latency_ms: float, success: bool = True) -> None:
        with self._lock:
            self._api_calls += 1
            self._api_latency.record(latency_ms)
            if not success:
                self._api_failures += 1

    def record_market_data_latency(self, latency_ms: float) -> None:
        self._market_data_latency.record(latency_ms)

    def set_token_expiry(self, expires_at_epoch: float) -> None:
        with self._lock:
            self._token_expires_at = expires_at_epoch

    def set_rate_limit_status(self, limited: bool, remaining: Optional[int] = None) -> None:
        with self._lock:
            self._rate_limited = limited
            self._rate_limit_remaining = remaining

    def snapshot(self) -> BrokerSnapshot:
        with self._lock:
            api_stats = self._api_latency.stats()
            md_stats = self._market_data_latency.stats()
            return BrokerSnapshot(
                connected=self._connected,
                token_expires_at=self._token_expires_at,
                reconnect_attempts=self._reconnect_attempts,
                api_failures=self._api_failures,
                api_calls=self._api_calls,
                api_latency_ms_avg=api_stats.average,
                api_latency_ms_p95=api_stats.p95,
                market_data_latency_ms_avg=md_stats.average,
                rate_limited=self._rate_limited,
                rate_limit_remaining=self._rate_limit_remaining,
            )

    def health(self) -> ComponentHealth:
        snap = self.snapshot()
        now = time.time()
        state = HealthState.HEALTHY
        reasons = []

        if not snap.connected:
            state = HealthState.OFFLINE
            reasons.append("not connected")
        elif snap.token_expires_at is not None and (snap.token_expires_at - now) < 0:
            state = HealthState.CRITICAL
            reasons.append("session token has expired")
        elif snap.token_expires_at is not None and (snap.token_expires_at - now) < self.token_expiry_warning_seconds:
            state = HealthState.WARNING
            reasons.append(f"token expires in under {self.token_expiry_warning_seconds:.0f}s")
        elif snap.rate_limited:
            state = HealthState.DEGRADED
            reasons.append("rate limited")
        elif snap.api_calls > 0 and (snap.api_failures / snap.api_calls) > 0.2:
            state = HealthState.DEGRADED
            reasons.append(f"{snap.api_failures}/{snap.api_calls} recent API calls failed")

        message = "; ".join(reasons) if reasons else "broker nominal"
        return ComponentHealth(
            name="broker", state=state, message=message,
            metrics={
                "connected": snap.connected, "token_expires_at": snap.token_expires_at,
                "reconnect_attempts": snap.reconnect_attempts, "api_failures": snap.api_failures,
                "api_calls": snap.api_calls, "api_latency_ms_avg": snap.api_latency_ms_avg,
                "api_latency_ms_p95": snap.api_latency_ms_p95,
                "market_data_latency_ms_avg": snap.market_data_latency_ms_avg,
                "rate_limited": snap.rate_limited, "rate_limit_remaining": snap.rate_limit_remaining,
            },
        )


# Process-wide default instance.
broker_monitor = BrokerMonitor()
