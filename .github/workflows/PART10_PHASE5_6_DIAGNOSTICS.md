# Part 10, Phases 5–6 — Runtime Diagnostics & Production Completion

Continues from `PART10_PHASE3_4_MONITORING.md` (Phases 3–4, already
delivered). Additive only — same rules apply: no trading logic,
Early Breakout Engine, Options Intelligence, Decision Engine, Replay
Engine, or Analytics Engine touched; no existing route's response
shape changed.

## New modules (`scripts/production/`)

| Module | Covers |
|---|---|
| `runtime_diagnostics.py` | Phase 5: `generate_startup_report()` (Environment/Configuration/Dependencies via the existing `startup_validator.py`, plus a Broker/Telegram/Scanner/Dashboard/Health snapshot) and `generate_runtime_report()` (uptime, avg scan/API time, peak CPU/memory, today's alerts/errors/failures/restarts, worker status, connection status, health score, production score, availability %). Owns three small trackers nothing else has: `ProcessUptimeTracker`, `PeakTracker`, `AvailabilityTracker`. |
| `performance_summary.py` | Phase 6: average Scanner/API/Dashboard/Replay/Analytics duration — Replay/Analytics derived by filtering `api_monitor`'s per-endpoint averages by `/api/replay` / `/api/analytics` path prefix, zero new instrumentation. |
| `service_registry.py` | Phase 6: component dependency map on top of `health_registry`; `register_known_components()` auto-registers all six Phase 1–3 monitors in one call; worker/background-task registry and monitoring registry both read through to `task_manager` / `health_registry` rather than duplicating their state. |
| `historical_metrics.py` | Phase 6: standalone bucketed time-series store at 1m/5m/15m/1h/daily granularity, with `cleanup()` for time-based pruning. |

## Modified files

- **`production/alert_engine.py`** — added `cleanup_history(max_age_seconds)`
  (Phase 6 "old alert cleanup"). No existing method's behavior changed.
- **`app/backend/api/app.py`** — added `diagnostics.router`; replaced
  the three individual `health_registry.register(...)` calls with
  `service_registry.register_known_components(...)`, which registers
  the same three (websocket/fastapi/dashboard) plus scanner/broker/
  telegram in one call and declares their dependency edges.

## New routes (`app/backend/api/routes/diagnostics.py`)

- `GET /api/diagnostics/runtime` — Phase 5 runtime report.
- `GET /api/diagnostics/performance` — Phase 6 performance summary.
- `GET /api/diagnostics/history/{metric}[?granularity=...]` — Phase 6
  historical rollups.
- `GET /api/diagnostics/services` — Phase 6 service discovery
  (dependency map, runtime status, worker registry, monitored-component
  list).

Full request/response shapes: `docs/API_DOCUMENTATION_PART10.md`.

## What still needs wiring (by design, not by omission)

- **`production_score`** on `/api/diagnostics/runtime` is always `null`
  from that route — `production_report.generate_production_report()`
  needs a config/startup report generated once at process startup, not
  something a per-request route can produce. Generate it once in
  `main.py`/`run_dashboard_api.py`'s startup path and pass it into
  `generate_runtime_report(production_report=...)` yourself if you
  want this populated.
- **`historical_metrics`** has no automatic feed — see the Monitoring
  Guide for the one-line `historical_metrics.record(...)` call to add
  wherever you want a metric tracked over time.
- **Cleanup scheduling** — `alert_engine.cleanup_history()` and
  `historical_metrics.cleanup()` are not called automatically; wire
  them into a `production.task_manager` background worker if running
  long enough for this to matter (see the Deployment Guide).

## Test results

```
python scripts/production/tests/test_part10_phase5.py   # 21/21 passed
python scripts/production/tests/test_part10_phase6.py   # 22/22 passed
```

Every pre-existing test file (Phase 1–6 production tests, Part 10
Phase 1–4) still passes unmodified — verified together:

```
for f in scripts/production/tests/test_*.py; do python "$f" || echo "FAILED: $f"; done
```

All 16 test files / ~200 tests pass with zero regressions.

## Documentation delivered this pass

`docs/ARCHITECTURE_PART10.md`, `docs/MONITORING_GUIDE.md`,
`docs/HEALTH_GUIDE.md`, `docs/API_DOCUMENTATION_PART10.md`,
`docs/DEPLOYMENT_GUIDE_PART10.md`, `docs/TROUBLESHOOTING_GUIDE_PART10.md`.

## Still not included: full Phase 6 test matrix

Phase 6 also asked for Load Tests specifically. Load testing needs a
running FastAPI process and a load-generation tool (e.g. `locust` or
`hey`) — genuinely different from the offline unit tests delivered
here (which don't spin up the ASGI app at all, matching the existing
`test_phase3_production.py`'s documented limitation for
`api_protection.py`'s middleware). Flagging this rather than faking a
load test that doesn't actually exercise the running server.
