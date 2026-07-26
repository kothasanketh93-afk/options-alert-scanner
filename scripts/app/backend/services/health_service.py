"""
health_service.py
------------------
Health signals from three honest sources:
  1. Today's state file (circuit breaker, api/broker failure counts)
     -- via state_reader, same as scanner_service.
  2. This dashboard process's own machine (CPU/mem/disk) via psutil,
     if installed -- this describes the *dashboard server*, not the
     GitHub Actions runner the scanner itself executes on (there is
     no way for this process to observe that runner's resource use,
     so it does not claim to).
  3. (Part 10) The Phase 3 poll-and-diff background task's own health
     -- live_service.py's per-component failure counts and watchdog
     status, surfaced under the "live_pipeline" key. See
     _live_pipeline_health() below for why this is a lazy import.

No queue size, cache hit ratio, or API-latency metrics are reported
here -- the existing app has no in-memory queue and no cache layer
that persists between GitHub Actions runs to measure, and no metrics
collection has been wired into main.py's HTTP calls yet. Fabricating
plausible-looking numbers for those would be worse than omitting them;
they're left out until something real is measured (see README.md).
"""

from app.backend.services import state_reader, error_handling

try:
    import psutil
    _HAS_PSUTIL = True
except ImportError:
    _HAS_PSUTIL = False


def _dashboard_process_metrics() -> dict:
    if not _HAS_PSUTIL:
        return {"available": False, "reason": "psutil not installed"}
    return {
        "available": True,
        "cpu_percent": psutil.cpu_percent(interval=0.1),
        "memory_percent": psutil.virtual_memory().percent,
        "disk_percent": psutil.disk_usage("/").percent,
    }


_EMPTY_LIVE_PIPELINE_HEALTH = {
    "failures": {"consecutive_failures": {}, "last_errors": {}},
    "watchdog": {
        "frozen": False, "never_completed_a_cycle": True,
        "seconds_since_last_cycle": None, "last_cycle_duration_seconds": None,
        "timeout_count": 0, "stall_threshold_seconds": None,
    },
}


def _live_pipeline_health() -> dict:
    """
    Part 10: surfaces the Phase 3 poll-and-diff background task's own
    health (services/live_service.py + watchdog.py) through the same
    /api/health score every existing consumer already reads, instead
    of adding a second endpoint for it.

    Lazy-imported inside this function, not at module top -- live_service
    already imports health_service at module level, so importing
    live_service here at module level would create a circular import.
    By the time get_health() is actually called (after the app has
    finished starting up), both modules are already fully loaded, so
    a local import here is safe and has no runtime cost worth avoiding.

    Reuses error_handling.safe_call() (added earlier in Part 10) rather
    than writing a second try/except pattern -- if live_service can't
    be reached for any reason (e.g. this function is called from a
    test that never started the app's background task), this falls
    back to _EMPTY_LIVE_PIPELINE_HEALTH so health reporting itself
    never becomes a new failure mode.
    """
    def _read():
        from app.backend.services import live_service
        return {
            "failures": live_service.get_failure_status(),
            "watchdog": live_service.get_watchdog_status(),
        }

    result, err = error_handling.safe_call(
        "live_pipeline", "health_service", "_live_pipeline_health", _read,
    )
    return result if err is None else _EMPTY_LIVE_PIPELINE_HEALTH


def get_health() -> dict:
    branch_status = state_reader.checkout_status()
    state = state_reader.get_state_for_date()
    daily_stats = state.get("daily_stats", {})
    live_pipeline = _live_pipeline_health()

    issues = []
    if not branch_status["ok"]:
        issues.append("state branch unreachable: " + str(branch_status["error"]))
    if state.get("circuit_breaker_tripped"):
        issues.append("circuit breaker is tripped")
    if state.get("_recovered_from_corruption"):
        issues.append("today's state file was recovered from corruption")
    if daily_stats.get("api_failures", 0) > 0:
        issues.append(f"{daily_stats['api_failures']} API failures recorded today")
    if daily_stats.get("broker_errors", 0) > 0:
        issues.append(f"{daily_stats['broker_errors']} broker errors recorded today")

    # Part 10 additions: the Phase 3 WebSocket pipeline's own health.
    watchdog_status = live_pipeline["watchdog"]
    if watchdog_status["frozen"]:
        issues.append(
            "live update pipeline (WebSocket poll loop) appears frozen: "
            f"no completed cycle in {watchdog_status['seconds_since_last_cycle']}s"
        )
    stuck_components = sorted(
        c for c, n in live_pipeline["failures"]["consecutive_failures"].items() if n >= 3
    )
    if stuck_components:
        issues.append(
            "live update pipeline components failing repeatedly: " + ", ".join(stuck_components)
        )

    # Simple, explainable score: start at 100, dock a fixed amount per
    # distinct issue category above, floor at 0. Not a statistical
    # model -- a transparent checklist, on purpose.
    score = max(0, 100 - 20 * len(issues))

    return {
        "status": "healthy" if score == 100 else ("degraded" if score >= 60 else "unhealthy"),
        "score": score,
        "issues": issues,
        "state_branch": branch_status,
        "dashboard_process": _dashboard_process_metrics(),
        "live_pipeline": live_pipeline,
    }
