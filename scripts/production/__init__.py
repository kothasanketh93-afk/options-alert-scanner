"""
scripts/production
-------------------
Part 9/10 production-readiness additions, kept in their own package so
that nothing in the existing scanner/decision-engine/replay/analytics/
dashboard code has to be touched to get them. Everything here is
additive and opt-in.

- config_validator.py -> pre-flight config checks (used by main.py)
- health_states.py -> shared health-state types (used by decision_engine's
  quant_adapter/news_adapter)

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

(2026-10-31, at the user's explicit request, as part of removing the
unused dashboard stack (app/backend/, run_dashboard_api.py -- not run by
any workflow): shutdown_manager, startup_validator, task_manager,
resource_monitor, health_registry, health_aggregator, the *_monitor
modules, alert_engine, api_protection, runtime_diagnostics,
historical_metrics, metrics_utils, performance_summary, service_registry
were deleted -- all reachable only from that stack.)
"""
