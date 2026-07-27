# Part 9 — Production Readiness: Migration Notes

Everything below is **additive**. No trading logic, Early Breakout logic,
Options Intelligence logic, Decision Engine logic, Replay Engine, Analytics
Engine, or existing Dashboard API route was rewritten, removed, or had its
behavior changed. Every original test suite (options_intelligence,
decision_engine, early_breakout, replay_analytics) still passes unmodified —
see "Test results" below.

All new code lives in one place: `scripts/production/`.

## How this is wired in

Unlike a purely opt-in library, the two real entry points now call into
Part 9 directly (small, additive edits -- see "Files modified" below for
exactly what changed in each):

- **`main.py`** (the per-run scanner, invoked every 5-15 min by
  `trader.yml`) — right after the existing `config_validation.
  validate_startup_config()` call, now also runs `production.
  config_validator.validate_config()` and logs any errors/warnings.
  **Never raises** — a config-validator bug or finding cannot change
  what a scan run does, only what gets logged. This is deliberately
  read-only/logging-only here: `main.py` is a short-lived, one-shot
  process, so there's no long-running state for
  `resource_monitor`/`task_manager`/`shutdown_manager` to manage.
- **`run_dashboard_api.py`** (the long-running dashboard process) — this
  is where the "long-running production system" pieces actually apply:
  - `run_startup_validation()` runs at startup with
    `require_upstox=False, require_angelone=False, require_github=False`
    (this process never touches broker credentials by design) and
    `raise_on_failure=False` (logs issues, doesn't refuse to start —
    matches this file's existing tolerant handling of a missing
    `REPO_URL`/`GITHUB_TOKEN`).
  - `resource_monitor` gets two gauges registered: `ws_connections`
    (from the existing `ws_manager.connection_count()`) and
    `background_task_count` (from `task_manager.running_count`).
  - `task_manager` runs one background worker, `resource_sampler`,
    that samples + logs resource alerts every 30s.
  - `shutdown_manager` registers a hook to stop all background workers
    on exit, and `main()`'s `uvicorn.run()` is now wrapped in
    `try/finally` so that hook always fires.
  - All of this feeds `GET /api/health/extended` automatically, since
    that route reads the same shared `resource_monitor`/`task_manager`
    singletons.

Everything else in the table below (retry_manager, cache_manager,
file_manager, performance.py, production_report.py) is still an opt-in
library — nothing forces `main.py` or `run_dashboard_api.py` to use them,
consistent with "do not rewrite working modules." A snippet for wiring
`production_report.py` in specifically is below.

## What was added

| Module | Part 9 item | What it does |
|---|---|---|
| `production/logging_manager.py` | 1. Centralized Logging | Per-channel rotating + gzip-archived log files (11 channels: scanner, decision_engine, replay, analytics, health, dashboard, api, broker, websocket, errors, performance), retention cleanup. |
| `exceptions.py` (extended) | 2. Exception Framework | Added `CircuitBreakerOpenError`, `RetryExhaustedError`, `StartupValidationError`, `ResourceLimitExceeded`, `WorkerError`, `CacheError` — all `TradingSystemError` subclasses. Nothing existing removed. |
| `production/retry_manager.py` | 3. Retry Framework | Generic retry + circuit breaker (exponential backoff, jitter, timeout, max attempts) for anything `retry_utils.py` doesn't already cover (config/replay loading, WebSocket reconnect, Telegram). `retry_utils.py` itself is untouched. |
| `production/startup_validator.py` | 4. Startup Validation | One `run_startup_validation()` call: Python version, dependencies, env/credentials (reuses `config_validation.py` as-is), config values, folders/permissions, required files, optional historical-data probe. Raises `StartupValidationError` (report attached) on critical failure so the caller can exit gracefully. |
| `production/config_validator.py` | 5. Configuration Validation | Read-only pass over `config.py`'s actual values: duplicate symbols, non-positive required fields, out-of-range fractions, invalid market-hour tuples, non-positive strike steps. Errors / warnings / recommendations, never raises by itself. |
| `production/resource_monitor.py` | 6. Resource Monitoring | Memory/CPU/disk (optional `psutil`, degrades gracefully if absent), thread count, and any custom gauge (queue size, WebSocket connections, background task count, API latency) via `register_gauge()`. Warning always fires before critical by construction. |
| `production/api_protection.py` | 7. API Protection | Pure-Python pagination + a shared JSON error envelope (dependency-free), plus `TimeoutMiddleware`/`RequestIDMiddleware` for FastAPI. Wired into `app/backend/api/app.py` (see below). |
| `production/task_manager.py` | 8. Background Task Manager | Named worker registry: no duplicate workers, no orphan threads, heartbeat + `check_health()`, graceful `stop`/`stop_all`, auto-restart on crash capped by `max_restarts`. |
| `production/cache_manager.py` | 9. Cache Improvements | `ManagedCache`: LRU + TTL, lazy loading with single-flight de-dup (`get_or_load`), validation hook, pattern-based safe invalidation (`invalidate_pattern`). `cache.py` / `daily_cache.py` untouched. |
| `production/performance.py` | 10. Performance Optimization | `@memoize` (wraps `ManagedCache`), `@timed` / `Timer` for measuring and logging slow spots to the `performance` log channel. |
| `production/file_manager.py` | 11. File Management | `ensure_directories`, `delete_expired_files`, `cleanup_temp_files`, `archive_old_reports` (gzip, no double-compression), `directory_size_bytes`. |
| `production/health_aggregator.py` + `app/backend/api/routes/health_extended.py` | 12. Health Improvements | `get_extended_health()` extends (does not modify) `health_service.get_health()` with resources, background tasks, and optional broker/Telegram/API/WebSocket/scan-schedule providers. New route `GET /api/health/extended` — the existing `GET /api/health` is completely untouched. |
| `production/shutdown_manager.py` | 13. Shutdown Handling | SIGINT/SIGTERM/atexit handling, ordered hook teardown, idempotent, tolerates a failing hook without blocking the rest. |
| `production/production_report.py` | 14. Production Report | `generate_production_report()` combines config/startup/health/cache-stats into one report: issues bucketed Critical/High/Medium/Low, performance/memory/API/thread summaries, a single readiness percentage, recommendations. |

## Files modified (not rewritten)

- **`scripts/exceptions.py`** — six new exception classes appended. Every
  existing class (`ConfigurationError`, `BrokerAPIError`, etc.) is untouched.
- **`scripts/app/backend/api/app.py`** — added two `app.add_middleware(...)`
  calls (`TimeoutMiddleware`, `RequestIDMiddleware`) and pointed the existing
  unhandled-exception handler at the shared error envelope (same status code
  and information, now with a request ID attached). Added one import line and
  one `include_router` line for the new `/api/health/extended` route. No
  route handler's logic, response shape, or existing status code changed.
- **`scripts/app/backend/settings.py`** — added one new setting,
  `API_REQUEST_TIMEOUT_SECONDS` (env override:
  `DASHBOARD_API_REQUEST_TIMEOUT_SECONDS`, default 10s). Nothing else changed.
- **`scripts/main.py`** — added a non-blocking config-value check (logs
  only, never raises) right after the existing `validate_startup_config()`
  call. No control flow, trading logic, or existing behavior changed.
- **`scripts/run_dashboard_api.py`** — added startup validation, resource
  gauge registration, one background sampler worker, and a shutdown hook
  (all detailed above). The existing `uvicorn.run(...)` call itself, and
  everything `create_app()` builds, is unchanged.

## Wiring the production report (still opt-in)

`production_report.py` is the one piece not auto-wired into either entry
point (it makes most sense run occasionally / on demand, not on every 5-min
scan cycle). Typical use, e.g. from `run_dashboard_api.py` on startup or
from a manual script:

```python
from production.config_validator import validate_config
from production.startup_validator import run_startup_validation
from production.health_aggregator import get_extended_health
from production.resource_monitor import resource_monitor
from production.task_manager import task_manager
from production.production_report import generate_production_report

cfg_report = validate_config()
startup_report = run_startup_validation(raise_on_failure=False, require_upstox=False,
                                         require_angelone=False, require_github=False)
health = get_extended_health(resource_monitor=resource_monitor, task_manager=task_manager)

report = generate_production_report(
    config_report=cfg_report, startup_report=startup_report, extended_health=health,
)
print(report.render())
```

## Backward compatibility

- `scripts/logging_setup.py`, `scripts/retry_utils.py`, `scripts/cache.py`,
  `scripts/daily_cache.py`, `scripts/config_validation.py` are **all
  untouched** — every existing caller keeps working exactly as before.
- `GET /api/health` response shape is unchanged.
- `requirements.txt` is unchanged — `psutil` (used optionally by
  `resource_monitor.py`) and `starlette`/`fastapi` (used optionally by
  `api_protection.py`'s middleware) are both guarded with `try/except
  ImportError` so the scanner's ephemeral GitHub Actions containers, which
  carry neither, are unaffected. If you want resource monitoring active in
  the dashboard process, add `psutil` to `requirements-dashboard.txt`.

## Test results

All new tests are plain-`assert` scripts (no pytest dependency), matching
this repo's existing convention (see `test_telegram.py` and the engine test
suites). Each phase file is directly runnable:

```
python scripts/production/tests/test_phase1_production.py   # 14/14 passed
python scripts/production/tests/test_phase2_production.py   # 12/12 passed
python scripts/production/tests/test_phase3_production.py   # 14/14 passed
python scripts/production/tests/test_phase4_production.py   # 17/17 passed
python scripts/production/tests/test_phase5_production.py   # 13/13 passed
python scripts/production/tests/test_phase6_production.py   # 15/15 passed
```

**85/85 new tests passing.** Full tree recompiles clean
(`python -m py_compile` over every `.py` file). Every pre-existing engine
test suite (options_intelligence 20/20, decision_engine all passed,
early_breakout 24/24, replay_analytics 38/38) still passes unmodified —
confirmed by re-running them after all six phases.

`production/api_protection.py`'s Starlette middleware classes
(`TimeoutMiddleware`, `RequestIDMiddleware`) are not unit-tested here —
`fastapi`/`starlette` aren't installable in this offline sandbox (same
limitation the original merge's `test_phase3_live.py` ran into, see
`MERGE_SUMMARY.pdf` §8). The pure-Python half of that module (pagination,
error envelope) is fully covered. **Recommendation:** install real
`fastapi`/`starlette`/`uvicorn` and smoke-test `GET /api/health/extended`
plus a deliberately slow route against `TimeoutMiddleware` before relying on
it in production.

## Known follow-ups (not blockers)

1. `run_startup_validation()`'s `historical_data_probe` has no default
   implementation in either wired entry point — pass a real one (e.g.
   checking the daily_cache instrument master exists for today) if you want
   that check to mean something more than "not assessed."
2. `health_aggregator.HealthProviders` (broker/telegram/api/scan-schedule —
   `websocket` status is wired via `ws_connections`, but not a dedicated
   status provider) are not registered yet — `/api/health/extended` reports
   them as `"not_configured"` until something calls
   `get_extended_health(providers=HealthProviders(...))` with real callables.
   `main.py` never imports `app.backend`, so broker/Telegram status can only
   realistically be published from there via a shared file/state key that
   `run_dashboard_api.py` then reads — not implemented here.
3. `production_report.py` itself is not scheduled anywhere (see wiring
   snippet above) — call it manually or add it to `run_dashboard_api.py`'s
   startup sequence if a periodic readiness report is wanted.
4. Per the original merge's own recommendation, `verify_stock_symbols.py`'s
   root-vs-`scripts/` duplication remains unaddressed — still out of scope
   here, same as before.
