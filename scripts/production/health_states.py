"""
production/health_states.py
------------------------------
Part 10, item 1: Health Monitoring -- shared vocabulary.

Every Part 10 monitor (system, scanner, broker, telegram, websocket,
api, background workers) reports its status as one of the same five
HealthState values, wrapped in the same ComponentHealth shape. Having
one shared enum/dataclass here -- rather than each monitor inventing
its own "ok"/"error"/"down" strings -- is what lets
health_registry.py (item 1's actual aggregator, see that module)
combine N independent component reports into one overall system state
with a single, uniform rule instead of N special cases.

This module has zero dependencies on anything else in the codebase --
safe to import from any monitor without creating a circular import.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from time import time
from typing import Any, Dict, List, Optional


class HealthState(IntEnum):
    """IntEnum, ordered worst-to-best is NOT the member order --
    ordering is HEALTHY < WARNING < DEGRADED < CRITICAL < OFFLINE so
    `max(states)` (used by combine()) naturally picks the worst state
    without a lookup table."""
    HEALTHY = 0
    WARNING = 1
    DEGRADED = 2
    CRITICAL = 3
    OFFLINE = 4

    def __str__(self) -> str:  # e.g. "HEALTHY" instead of "HealthState.HEALTHY" in reports/logs
        return self.name


@dataclass
class ComponentHealth:
    """One component's health at one point in time. `metrics` is a
    free-form dict -- each monitor puts whatever numbers are relevant
    to it (e.g. scanner puts scan_duration_seconds, broker puts
    token_expiry_seconds) so this one shape covers every component
    without a subclass per component."""
    name: str
    state: HealthState
    message: str
    checked_at: float = field(default_factory=time)
    metrics: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "state": str(self.state),
            "message": self.message,
            "checked_at": self.checked_at,
            "metrics": self.metrics,
        }


def combine(states: List[HealthState]) -> HealthState:
    """Worst-of-N: overall health is never better than its worst
    component. An empty list is HEALTHY (nothing reported anything
    wrong) rather than an error -- callers with zero components
    registered get a neutral, not alarming, default."""
    if not states:
        return HealthState.HEALTHY
    return max(states)


def combine_components(components: List[ComponentHealth]) -> HealthState:
    return combine([c.state for c in components])


# Thresholds shared by any monitor that just needs "is this percentage
# concerning" without its own bespoke scale (system_monitor.py uses
# these; other monitors define their own where percentages don't
# apply, e.g. broker reconnect counts).
DEFAULT_WARNING_PCT = 75.0
DEFAULT_DEGRADED_PCT = 90.0
DEFAULT_CRITICAL_PCT = 97.0


def state_from_pct(value: Optional[float], warning: float = DEFAULT_WARNING_PCT,
                    degraded: float = DEFAULT_DEGRADED_PCT,
                    critical: float = DEFAULT_CRITICAL_PCT) -> HealthState:
    """Maps a 0-100 percentage to a HealthState using three
    thresholds. None (metric unavailable, e.g. psutil not installed)
    maps to WARNING, not HEALTHY -- "we don't know" should never look
    the same as "confirmed fine" on a dashboard."""
    if value is None:
        return HealthState.WARNING
    if value >= critical:
        return HealthState.CRITICAL
    if value >= degraded:
        return HealthState.DEGRADED
    if value >= warning:
        return HealthState.WARNING
    return HealthState.HEALTHY
