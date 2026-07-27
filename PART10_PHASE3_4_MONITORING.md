# Part 10, Phases 3–4 — Live Dashboard Monitoring & Alerting Engine

Continues directly from Part 10 Phase 1 (Health Monitoring) and Phase 2
(Scanner/Broker/Telegram Monitoring), already completed. Everything below is
**additive** — no trading logic, Early Breakout Engine, Options Intelligence,
Decision Engine, Replay Engine, or Analytics Engine was touched. No existing
route's response shape changed. Every pre-existing test suite still passes
unmodified (see "Test results" below).

## New modules (`scripts/production/`)

| Module | Covers |
|---|---|
| `websocket_monitor.py` | Connected/active/disconnected clients, reconnect count/success, broadcast count/failures/avg/max time, dropped messages, connection duration, ping/latency, last broadcast, current subscribers. |
| `api_monitor.py` | Per-endpoint request/success/failure counts, avg/p95/max/min response time, concurrent requests, slow requests, rejected requests, validation errors, rate-limit events. Includes an optional Starlette `APIMonitoringMiddleware` (same lazy-import guard as `api_protection.py`). |
| `dashboard_monitor.py` | Dashboard availability, response time/requests/errors, active sessions, WebSocket client count, refresh time, health — deliberately thin, reads `api_monitor`/`websocket_monitor` rather than duplicating their counters (see its docstring). |
| `alert_engine.py` | Phase 4 Alerting Engine: CPU/memory/disk/queue thresholds (via `resource_monitor`), broker disconnected, Telegram unavailable, scanner inactive, API latency high, WebSocket failures, worker failures, repeated exceptions — each rule maps to `AlertSeverity.INFO/WARNING/ERROR/CRITICAL`. Supports cooldown, suppression, aggregation, and history. |

All four follow the exact pattern already established in Phase 1/2
(`scanner_monitor.py`, `broker_monitor.py`, `telegram_monitor.py`,
`health_registry.py`): a plain-Python class with `record_*()` methods, a
`RollingWindow`-backed snapshot, a `health()` returning `ComponentHealth`, and
one process-wide default instance. No new module imports scanner.py,
broker.py, main.py, or any engine directly.

## Files modified (small, additive edits only)

- **`app/backend/api/ws_manager.py`** — `ConnectionManager.connect()` /
  `disconnect()` / `broadcast()` each gained one call into
  `websocket_monitor` (`record_connect`/`record_disconnect`/
  `record_broadcast`). No change to connection behavior.
- **`app/backend/services/live_service.py`** — `poll_loop()` gained one call
  to `dashboard_monitor.record_refresh()` after each cycle completes,
  reusing the cycle duration `watchdog.CycleWatchdog` already computes
  (no duplicate timing logic).
- **`app/backend/api/app.py`**:
  - Added `APIMonitoringMiddleware` (added last, so it wraps
    `RequestIDMiddleware`/`TimeoutMiddleware` and times what a client
    actually experiences).
  - Registered `websocket_monitor.health`, `api_monitor.health`, and
    `dashboard_monitor.health` with the existing `health_registry`
    (Part 10 item 1) at startup — these three components previously
    reported "not monitored" OFFLINE from `health_registry.check_all()`;
    they now report real data.
  - `dashboard_monitor.set_available(False)` on shutdown.
  - Registered the new `monitoring` router.
  - Bumped `API_VERSION` to `"0.4.0-phase3-4-monitoring"`.

## New routes (`app/backend/api/routes/monitoring.py`)

All read-only, all additive (nothing in `routes/health.py` or
`routes/health_extended.py` changed):

- `GET /api/monitoring/websocket` — WebSocket monitoring snapshot.
- `GET /api/monitoring/api` — API monitoring totals + per-endpoint
  breakdown; `?endpoint=/api/health` filters to one endpoint.
- `GET /api/monitoring/dashboard` — Dashboard monitoring snapshot.
- `GET /api/monitoring/alerts` — runs every Phase 4 alert rule against the
  current monitor state and returns `fired_this_call`, `active` (within the
  current cooldown window), and `history` (optionally filtered with
  `?min_severity=WARNING`).

## Wiring worker-failure / repeated-exception alerts (not automatic)

`alert_engine.evaluate()` takes an `AlertSources` dataclass — every field is
optional, same opt-in pattern as `health_aggregator.HealthProviders`. The
`/api/monitoring/alerts` route wires up everything it can reach directly
(`resource_monitor`, `broker_monitor`, `telegram_monitor`, `scanner_monitor`,
`api_monitor`, `websocket_monitor`, `task_manager`). It does **not** wire
`exception_counts` — that needs `error_handling.ComponentFailureTracker`
instances that live inside specific services (e.g.
`live_service.get_failure_status()`); pass
`exception_counts=lambda: live_service.get_failure_status()["consecutive_failures"]`
if you want that rule active too.

## Running Phase 4 continuously (optional)

The route above evaluates alerts on every GET, which is enough for a
dashboard that polls it. For alerting even when nobody is looking at the
dashboard, register a `production.task_manager` background worker that calls
`alert_engine.evaluate(sources)` on a timer and does something with the
result (log, push a Telegram message via the existing `telegram_notify.py`,
etc.). This is deliberately left as a wiring decision for whoever owns
alert delivery, not built into `alert_engine.py` itself — the engine's job
is producing correctly-severitized, deduplicated alerts, not deciding how
they're delivered.

## Test results

```
python scripts/production/tests/test_part10_phase3.py   # 25/25 passed
python scripts/production/tests/test_part10_phase4.py   # 14/14 passed
```

Every pre-existing `scripts/production/tests/test_*.py` file (Phase 1–6
production tests, Part 10 Phase 1/2) still passes unmodified after these
changes — re-run them together to confirm in your environment:

```
for f in scripts/production/tests/test_*.py; do python "$f" || echo "FAILED: $f"; done
```

`APIMonitoringMiddleware` and the FastAPI-dependent parts of `app.py`/
`ws_manager.py`/`monitoring.py` are not exercised by the offline test suite
above (this sandbox has no `fastapi`/`starlette` installed, same limitation
`test_phase3_production.py` already documents for `api_protection.py`'s
middleware classes). Exercise those by running the dashboard
(`run_dashboard_api.py`) in an environment with `requirements-dashboard.txt`
installed and hitting `/api/monitoring/*`.

## Scope note — Phases 5 & 6 not included in this pass

The full spec also asks for Phase 5 (Runtime Diagnostics — startup/runtime
reports, health/production scores, availability %), Phase 6 (Production
Completion — service discovery, component dependency map, historical-metrics
rollups at 1m/5m/15m/1h/daily granularity, automatic cleanup), plus
architecture/API/deployment/troubleshooting docs and load tests. That's a
substantial further scope on top of Phases 3–4 above. Flagging it explicitly
rather than delivering a thin/rushed version of six phases at once — happy
to continue with Phase 5 next.
