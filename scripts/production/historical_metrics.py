"""
production/historical_metrics.py
------------------------------------
Part 10, Phase 6: Historical Metrics.

Maintains a rolling, in-memory time series per named metric and rolls
it up into fixed-size buckets at 1 minute / 5 minutes / 15 minutes /
1 hour / daily granularity, for trend analysis (e.g. "has average scan
time been creeping up over the last hour"). Same in-memory,
dependency-free philosophy as metrics_utils.RollingWindow (see that
module's docstring) -- this is deliberately not a time-series
database, just bounded-memory bucketed averages.

Distinct from RollingWindow.trend(): that method buckets *by sample
count* (last N samples split into K chunks), useful when you don't
know or care about wall-clock spacing. This module buckets *by
wall-clock time* (every sample tagged with its timestamp, grouped into
fixed-duration buckets), which is what "1 minute / 5 minute / ..."
granularity actually means -- the two are complementary, not
duplicates.

Also owns Phase 6's "Cleanup" requirement for this module's own data:
raw samples older than `raw_retention_seconds` are dropped by
cleanup(), so memory is bounded by retention window x metric count,
not by process lifetime x sample rate.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

# The five granularities Phase 6 asks for, in seconds.
ONE_MINUTE = 60
FIVE_MINUTES = 300
FIFTEEN_MINUTES = 900
ONE_HOUR = 3600
DAILY = 86400

GRANULARITIES = (ONE_MINUTE, FIVE_MINUTES, FIFTEEN_MINUTES, ONE_HOUR, DAILY)

# Raw samples older than this are eligible for cleanup() to drop.
# Generous enough to still compute a meaningful DAILY rollup (which
# needs samples spanning up to a day) while bounding memory in a
# long-running process -- see cleanup()'s docstring.
DEFAULT_RAW_RETENTION_SECONDS = 2 * DAILY


@dataclass
class Bucket:
    bucket_start: float
    average: float
    count: int
    minimum: float
    maximum: float


class HistoricalMetricsStore:
    def __init__(self, raw_retention_seconds: float = DEFAULT_RAW_RETENTION_SECONDS):
        self._lock = threading.Lock()
        self._samples: Dict[str, List[Tuple[float, float]]] = {}  # metric -> [(timestamp, value), ...]
        self.raw_retention_seconds = raw_retention_seconds

    def record(self, metric: str, value: float, at: Optional[float] = None) -> None:
        with self._lock:
            self._samples.setdefault(metric, []).append((at if at is not None else time.time(), value))

    def metric_names(self) -> List[str]:
        with self._lock:
            return sorted(self._samples.keys())

    def rollup(self, metric: str, granularity_seconds: int, lookback_seconds: Optional[float] = None) -> List[Bucket]:
        """Groups this metric's samples into contiguous
        `granularity_seconds`-wide buckets aligned to epoch (bucket_start
        = floor(timestamp / granularity) * granularity), oldest first.
        `lookback_seconds` (default: unset, i.e. all retained samples)
        limits how far back to look; pass e.g. 3600 to only see the
        last hour of 1-minute buckets."""
        with self._lock:
            samples = list(self._samples.get(metric, []))
        if not samples:
            return []
        now = time.time()
        cutoff = now - lookback_seconds if lookback_seconds is not None else 0.0
        samples = [(t, v) for t, v in samples if t >= cutoff]
        if not samples:
            return []

        buckets: Dict[float, List[float]] = {}
        for t, v in samples:
            bucket_start = (t // granularity_seconds) * granularity_seconds
            buckets.setdefault(bucket_start, []).append(v)

        return [
            Bucket(
                bucket_start=start, average=round(sum(vals) / len(vals), 4),
                count=len(vals), minimum=min(vals), maximum=max(vals),
            )
            for start, vals in sorted(buckets.items())
        ]

    def all_rollups(self, metric: str) -> Dict[str, List[Bucket]]:
        """One rollup per Phase 6 granularity, keyed by a readable
        label rather than the raw seconds value."""
        labels = {
            ONE_MINUTE: "1_minute", FIVE_MINUTES: "5_minutes", FIFTEEN_MINUTES: "15_minutes",
            ONE_HOUR: "1_hour", DAILY: "daily",
        }
        return {labels[g]: self.rollup(metric, g) for g in GRANULARITIES}

    def cleanup(self, retention_seconds: Optional[float] = None) -> int:
        """Drops raw samples older than `retention_seconds` (default:
        self.raw_retention_seconds) across every metric. Returns the
        number of samples dropped. Safe to call on a schedule (e.g.
        from a production.task_manager background worker) -- this is
        Phase 6's 'automatic metric cleanup' / 'memory optimization'
        requirement for this module's own storage."""
        cutoff = time.time() - (retention_seconds if retention_seconds is not None else self.raw_retention_seconds)
        dropped = 0
        with self._lock:
            for metric, samples in self._samples.items():
                kept = [(t, v) for t, v in samples if t >= cutoff]
                dropped += len(samples) - len(kept)
                self._samples[metric] = kept
        return dropped

    def sample_count(self, metric: Optional[str] = None) -> int:
        with self._lock:
            if metric is not None:
                return len(self._samples.get(metric, []))
            return sum(len(v) for v in self._samples.values())


# Process-wide default instance.
historical_metrics = HistoricalMetricsStore()
