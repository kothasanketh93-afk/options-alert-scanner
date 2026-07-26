"""
app/backend
-----------
Part 8 (Phases 1-3) -- Backend Service Layer, Core REST APIs,
Analytics/Replay REST surface, and poll-and-diff WebSocket streaming.

This package is a READ-ONLY monitoring layer on top of the existing
scanner. It never places orders, never writes to the `state` git
branch, never touches config.py, broker.py, auth.py, or any engine's
business logic. It only *reads* what those already produce (state
files, config constants, replay_analytics' persisted journal/reports)
and exposes them over HTTP and WebSocket for a dashboard.

Design constraint that shapes everything here: the scanner is not a
long-running server -- it's a batch job (trader.yml) invoked on a
GitHub Actions schedule, which loads state/<date>.json from the
`state` git branch, mutates it, and pushes it back (see state.py).
There is no in-memory "live" scanner process to query.

So this backend runs as its own separate, optional process (see
run_dashboard_api.py) that re-reads the state branch on each request
(or on a short poll interval -- see services/state_reader.py), rather
than being wired into main.py's run loop. That keeps it fully
decoupled: main.py, trader.yml, and every existing module are
completely unmodified by Part 8.

Scope so far:
  - Phase 1: ScannerService, HealthService, ConfigurationService,
    DashboardService + /api/health, /api/scanner/*, /api/signals/*,
    /api/config, /api/dashboard/summary, /api/version.
  - Phase 2: ReplayService, AnalyticsService (reuse
    app/engines/replay_analytics/, never trigger a replay run, never
    write) + /api/replay/*, /api/analytics/*.
  - Phase 3: poll-and-diff WebSocket streaming at /ws/dashboard (see
    services/live_service.py). Explicit design decision: poll-and-diff,
    not publish-on-write -- this process still never touches main.py.

Explicitly NOT in this pass (left as later phases, not fabricated):
  - React dashboard UI (Phase 4)
  - Export (CSV/JSON/Excel/PDF), search, filters (Phase 5)
  - Configuration reload/validate/export/import (Phase 6)
  - Notifications, alert monitor, error management, structured logging
    (Phase 7)
  - Auth/rate limiting on this API, real latency instrumentation
    (Phase 8)
  - Full integration/UI/perf/security test suite, docs site (Phase 9)
See README.md in this directory for the honest state of what's done
vs. stubbed.
"""
