"""
scripts/production
-------------------
Part 9/10 production-readiness additions, kept in their own package so
that nothing in the existing scanner/decision-engine/replay/analytics/
dashboard code has to be touched to get them. Everything here is
additive and opt-in.

- shutdown_manager.py  -> coordinated graceful shutdown
- config_validator.py / startup_validator.py -> pre-flight checks
- task_manager.py / resource_monitor.py / health_* / *_monitor.py ->
  background-worker supervision and health reporting, used by the
  optional dashboard (app/backend/)

Existing modules (logging_setup.py, retry_utils.py, exceptions.py,
config_validation.py, cache.py) are NOT replaced. Callers that already
work continue to work unmodified.

(2026-10-30, at the user's explicit request, as part of removing unused
code to make this app intraday-only: logging_manager.py, retry_manager.py,
cache_manager.py, file_manager.py, performance.py, and production_report.py
were deleted -- each had zero importers anywhere in the repo outside its
own now-also-updated test file. They were meant to be optionally migrated
onto by existing code (logging_setup.py, retry_utils.py, cache.py) but
that migration never happened.)
"""
