"""
production/metrics_utils.py
------------------------------
Shared by Part 10 items 3-9 (Scanner/Broker/Telegram/WebSocket/API
Monitoring, Metrics Collection): a small, dependency-free rolling
window for numeric samples (durations, latencies, counts), so
"average", "95th percentile", "max" and "trend over the last N
samples" are computed the same way everywhere instead of five
slightly-different reimplementations.

Deliberately in-memory only, bounded by max_samples (a deque, not an
unbounded list) -- matches this codebase's established preference
(see cache.py's docstring) for the simplest mechanism that solves the
actual problem, and Part 9/10's own "store rolling metrics in memory"
requirement (item 9) rather than a time-series database.
"""

from __future__ import annotations

import statistics
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, Optional, Tuple


@dataclass
class RollingStats:
    count: int
    average: Optional[float]
    p95: Optional[float]
    maximum: Optional[float]
    minimum: Optional[float]
    latest: Optional[float]


class RollingWindow:
    """Thread-safe fixed-capacity window of (timestamp, value) samples.
    Oldest sample is dropped once max_samples is exceeded (a deque
    with maxlen does this for free, O(1))."""

    def __init__(self, max_samples: int = 500):
        self._samples: Deque[Tuple[float, float]] = deque(maxlen=max_samples)
        self._lock = threading.Lock()

    def record(self, value: float, at: Optional[float] = None) -> None:
        with self._lock:
            self._samples.append((at if at is not None else time.time(), value))

    def stats(self) -> RollingStats:
        with self._lock:
            values = [v for _, v in self._samples]
        if not values:
            return RollingStats(count=0, average=None, p95=None, maximum=None, minimum=None, latest=None)
        sorted_values = sorted(values)
        p95_index = min(len(sorted_values) - 1, int(round(0.95 * (len(sorted_values) - 1))))
        return RollingStats(
            count=len(values),
            average=statistics.fmean(values),
            p95=sorted_values[p95_index],
            maximum=max(values),
            minimum=min(values),
            latest=values[-1],
        )

    def trend(self, buckets: int = 10) -> list:
        """Splits the current window into `buckets` contiguous chunks
        (oldest first) and averages each -- a cheap, dependency-free
        way to answer "is this trending up or down" (Part 10 item 9)
        without a real time-series store. Returns fewer than `buckets`
        entries if there aren't enough samples yet."""
        with self._lock:
            values = [v for _, v in self._samples]
        if not values:
            return []
        chunk_size = max(1, len(values) // buckets)
        return [
            round(statistics.fmean(values[i:i + chunk_size]), 4)
            for i in range(0, len(values), chunk_size)
        ]

    def clear(self) -> None:
        with self._lock:
            self._samples.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._samples)
