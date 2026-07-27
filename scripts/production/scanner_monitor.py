"""
production/scanner_monitor.py
--------------------------------
Part 10, item 3: Scanner Monitoring.

Tracks current/last/next scan, average + latest scan duration, skipped
and failed scan counts, success %, and today's alert count -- purely
by recording events (scan_started/scan_completed/scan_skipped/
scan_failed). Does NOT import scanner.py, main.py, or touch any
trading logic: main.py calls these methods around its existing
run_scanner_step() call (an additive one-line wrap, see migration
notes), exactly the same decoupled-observer pattern as every other
Part 9/10 monitor.

"Today" (for alert_count / success %% "today") is caller-defined --
this module doesn't know about IST trading days or state.py's date
handling, so it exposes reset_daily_counters() for the caller (main.py
already knows the trading date) to call once per new trading day,
rather than this module guessing at day boundaries itself.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Optional

from production.metrics_utils import RollingWindow
from production.health_states import ComponentHealth, HealthState

# A scan that hasn't completed within this many seconds of starting is
# considered possibly hung for health-reporting purposes (separate
# from any watchdog-driven restart decision -- item 11 owns that).
DEFAULT_STALL_SECONDS = 300.0
# No completed scan within this many seconds -> OFFLINE rather than
# just DEGRADED (e.g. main.py hasn't run in over an hour on a 5-min
# schedule -- worth flagging loudly).
DEFAULT_NO_ACTIVITY_SECONDS = 3600.0


@dataclass
class ScannerSnapshot:
    current_scan_started_at: Optional[float]
    last_scan_at: Optional[float]
    next_scan_at: Optional[float]
    last_scan_duration_seconds: Optional[float]
    average_scan_time_seconds: Optional[float]
    scan_count: int
    skipped_scans: int
    failed_scans: int
    success_pct: Optional[float]
    alerts_today: int


class ScannerMonitor:
    def __init__(self, stall_seconds: float = DEFAULT_STALL_SECONDS,
                 no_activity_seconds: float = DEFAULT_NO_ACTIVITY_SECONDS):
        self._lock = threading.Lock()
        self._durations = RollingWindow(max_samples=200)
        self._current_scan_started_at: Optional[float] = None
        self._last_scan_at: Optional[float] = None
        self._next_scan_at: Optional[float] = None
        self._scan_count = 0
        self._skipped_scans = 0
        self._failed_scans = 0
        self._alerts_today = 0
        self.stall_seconds = stall_seconds
        self.no_activity_seconds = no_activity_seconds

    def scan_started(self) -> None:
        with self._lock:
            self._current_scan_started_at = time.time()

    def scan_completed(self, alert_count: int = 0) -> None:
        with self._lock:
            now = time.time()
            if self._current_scan_started_at is not None:
                self._durations.record(now - self._current_scan_started_at, at=now)
            self._current_scan_started_at = None
            self._last_scan_at = now
            self._scan_count += 1
            self._alerts_today += alert_count

    def scan_skipped(self) -> None:
        with self._lock:
            self._current_scan_started_at = None
            self._skipped_scans += 1

    def scan_failed(self) -> None:
        with self._lock:
            self._current_scan_started_at = None
            self._failed_scans += 1

    def set_next_scan_at(self, when: float) -> None:
        """Caller-supplied (e.g. from a cron schedule or a fixed
        interval main.py knows about) -- this module has no schedule
        of its own to derive it from."""
        with self._lock:
            self._next_scan_at = when

    def reset_daily_counters(self) -> None:
        """Call once per new trading day (main.py owns date logic).
        Resets alerts_today; scan_count/skipped/failed/duration
        history are cumulative-for-process, not reset here, since
        "today's alert count" is the only one Part 10 item 3
        explicitly scopes to a day."""
        with self._lock:
            self._alerts_today = 0

    def snapshot(self) -> ScannerSnapshot:
        with self._lock:
            stats = self._durations.stats()
            total_attempts = self._scan_count + self._failed_scans
            success_pct = (self._scan_count / total_attempts * 100.0) if total_attempts else None
            return ScannerSnapshot(
                current_scan_started_at=self._current_scan_started_at,
                last_scan_at=self._last_scan_at,
                next_scan_at=self._next_scan_at,
                last_scan_duration_seconds=stats.latest,
                average_scan_time_seconds=stats.average,
                scan_count=self._scan_count,
                skipped_scans=self._skipped_scans,
                failed_scans=self._failed_scans,
                success_pct=success_pct,
                alerts_today=self._alerts_today,
            )

    def health(self) -> ComponentHealth:
        snap = self.snapshot()
        now = time.time()
        state = HealthState.HEALTHY
        reasons = []

        if snap.current_scan_started_at is not None and (now - snap.current_scan_started_at) > self.stall_seconds:
            state = HealthState.CRITICAL
            reasons.append(f"current scan running for over {self.stall_seconds:.0f}s -- possibly stalled")
        elif snap.last_scan_at is None:
            state = HealthState.WARNING
            reasons.append("no scan has completed yet")
        elif (now - snap.last_scan_at) > self.no_activity_seconds:
            state = HealthState.OFFLINE
            reasons.append(f"no completed scan in over {self.no_activity_seconds:.0f}s")
        elif snap.success_pct is not None and snap.success_pct < 80.0:
            state = HealthState.DEGRADED
            reasons.append(f"success rate {snap.success_pct:.1f}% is below 80%")

        message = "; ".join(reasons) if reasons else "scanner nominal"
        return ComponentHealth(
            name="scanner", state=state, message=message,
            metrics={
                "last_scan_at": snap.last_scan_at, "next_scan_at": snap.next_scan_at,
                "last_scan_duration_seconds": snap.last_scan_duration_seconds,
                "average_scan_time_seconds": snap.average_scan_time_seconds,
                "scan_count": snap.scan_count, "skipped_scans": snap.skipped_scans,
                "failed_scans": snap.failed_scans, "success_pct": snap.success_pct,
                "alerts_today": snap.alerts_today,
            },
        )


# Process-wide default instance.
scanner_monitor = ScannerMonitor()
