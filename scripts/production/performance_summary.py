"""
production/performance_summary.py
-------------------------------------
Part 10, Phase 6: Performance Summary.

Average Scanner / API / Dashboard / Replay / Analytics duration --
derived entirely from monitors that already exist, with zero new
instrumentation: api_monitor.py's APIMonitoringMiddleware already
times *every* HTTP route (added in Phase 3), so "Replay Duration" and
"Analytics Duration" are simply the average response time of whatever
endpoints live under /api/replay and /api/analytics respectively --
no separate timing hook needed inside replay_service.py or
analytics_service.py (and therefore nothing added to the Replay Engine
or Analytics Engine, per the modification rules).

"Dashboard Duration" reuses dashboard_monitor's refresh-time average
(the poll-and-diff cycle, Phase 3); "Scanner Duration" reuses
scanner_monitor's average_scan_time_seconds (Phase 2); "API Duration"
is api_monitor's average across every endpoint.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

REPLAY_PATH_PREFIX = "/api/replay"
ANALYTICS_PATH_PREFIX = "/api/analytics"


@dataclass
class PerformanceSummary:
    average_scanner_duration_seconds: Optional[float]
    average_api_duration_ms: Optional[float]
    average_dashboard_duration_seconds: Optional[float]
    average_replay_duration_ms: Optional[float]
    average_analytics_duration_ms: Optional[float]

    def to_dict(self) -> dict:
        return {
            "average_scanner_duration_seconds": self.average_scanner_duration_seconds,
            "average_api_duration_ms": self.average_api_duration_ms,
            "average_dashboard_duration_seconds": self.average_dashboard_duration_seconds,
            "average_replay_duration_ms": self.average_replay_duration_ms,
            "average_analytics_duration_ms": self.average_analytics_duration_ms,
        }


def _average_for_prefix(api_monitor, prefix: str) -> Optional[float]:
    snapshots = api_monitor.all_snapshots()
    times = [
        s.avg_response_time_ms for path, s in snapshots.items()
        if path.startswith(prefix) and s.avg_response_time_ms is not None
    ]
    if not times:
        return None
    return round(sum(times) / len(times), 2)


def generate_performance_summary(
    scanner_monitor=None,
    api_monitor=None,
    dashboard_monitor=None,
) -> PerformanceSummary:
    """Every argument optional -- an omitted monitor leaves its field
    None rather than raising, same "not assessed" rule as every other
    Part 9/10 report."""
    scanner_duration = (
        scanner_monitor.snapshot().average_scan_time_seconds if scanner_monitor is not None else None
    )
    dashboard_duration = (
        dashboard_monitor.snapshot().avg_refresh_time_seconds if dashboard_monitor is not None else None
    )

    api_duration = None
    replay_duration = None
    analytics_duration = None
    if api_monitor is not None:
        totals = [
            s.avg_response_time_ms for s in api_monitor.all_snapshots().values()
            if s.avg_response_time_ms is not None
        ]
        api_duration = round(sum(totals) / len(totals), 2) if totals else None
        replay_duration = _average_for_prefix(api_monitor, REPLAY_PATH_PREFIX)
        analytics_duration = _average_for_prefix(api_monitor, ANALYTICS_PATH_PREFIX)

    return PerformanceSummary(
        average_scanner_duration_seconds=scanner_duration,
        average_api_duration_ms=api_duration,
        average_dashboard_duration_seconds=dashboard_duration,
        average_replay_duration_ms=replay_duration,
        average_analytics_duration_ms=analytics_duration,
    )
