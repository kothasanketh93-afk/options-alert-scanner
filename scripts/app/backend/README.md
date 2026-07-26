# Dashboard Backend -- Phase 1 + Phase 2 + Phase 3 (Part 8 spec)

Read-only backend service layer + REST API over the existing scanner:
core scanner/health/config monitoring (Phase 1), analytics and
replay-journal access (Phase 2), plus a poll-and-diff WebSocket for
live updates (Phase 3). This is **still a multi-phase build** -- the
full Part 8 spec (a React dashboard, export to CSV/JSON/Excel/PDF,
full-text search, config hot-reload, notification wiring, rate
limiting, a full test/security/observability suite, and docs beyond
this file) is not attempted in one pass. See "What's genuinely
implemented vs. deliberately left out" below before assuming something
works.

## What this reuses vs. adds

Reuses, unmodified: `state.py`, `config.py`, and everything they
depend on. Nothing in `scripts/` outside `app/backend/` and
`run_dashboard_api.py` was touched.

Genuinely new (there was no existing web/API layer to reuse):
`app/backend/` and `run_dashboard_api.py`.

## Architecture note: this is a separate process

The scanner (`main.py`) is a batch job triggered by `trader.yml` on a
GitHub Actions schedule -- it is not a long-running server, and there
is no in-memory "live" scanner to query. State lives as JSON files on
a dedicated `state` git branch (see `state.py`).

So `run_dashboard_api.py` runs as its **own separate process**,
wherever you choose to host it, and re-pulls the `state` branch
read-only (at most once every `DASHBOARD_STATE_REFRESH_SECONDS`,
default 15s -- see `settings.py`) to answer requests. It never writes
to that branch (`allow_create=False` is hardcoded in `settings.py`),
never imports `broker.py`/`auth.py`, and has no broker credentials.

## Running it

```
export REPO_URL=https://github.com/you/repo.git
export GITHUB_TOKEN=<token, read access to REPO_URL is enough>
pip install -r requirements-dashboard.txt   # kept separate from
                                             # requirements.txt on
                                             # purpose -- see that
                                             # file's header comment
python run_dashboard_api.py
```

Then: `http://127.0.0.1:8800/docs` for interactive Swagger docs, or:

| Endpoint | What it returns |
|---|---|
| `GET /api/health` | state-branch reachability, circuit breaker, today's failure counters, dashboard-process CPU/mem/disk |
| `GET /api/scanner/status?date=` | today's (or a given day's) trade counts, circuit breaker, daily_stats |
| `GET /api/scanner/open-trades?date=` | trades currently WAITING_ENTRY / ACTIVE / T1_HIT |
| `GET /api/signals/latest?date=&limit=` | all trades for a day, newest alert first |
| `GET /api/signals/history?date=` | full trade list for one past day (date required) |
| `GET /api/signals/history/available-dates?lookback_days=` | which past dates actually have a state file |
| `GET /api/config` | read-only snapshot of `config.py`'s public constants (secret-shaped names redacted) |
| `GET /api/dashboard/summary` | health + status + latest signals in one call |
| `GET /api/version` | API version string |

**Phase 2 -- Analytics & Replay** (reuses `app/engines/replay_analytics/`, never triggers a replay run, never writes):

| Endpoint | What it returns |
|---|---|
| `GET /api/replay/journal?date=` | that day's trade journal entries (empty list if the replay job hasn't run for that date) |
| `GET /api/replay/journal/range?start_date=&end_date=` | journal entries across a date range |
| `GET /api/replay/summary/daily?date=` | the pre-generated daily report, or `available: false` if it hasn't been generated yet |
| `GET /api/replay/summary/weekly?date=` | the pre-generated weekly report covering the week containing `date`, or `available: false` |
| `GET /api/replay/summary/monthly?year_month=YYYY-MM` | the pre-generated monthly report, or `available: false` |
| `GET /api/analytics/performance?start_date=&end_date=` | win rate, profit factor, expectancy, drawdown, streaks -- computed on demand over the journal, not persisted |
| `GET /api/analytics/statistics?start_date=&end_date=` | performance + avg confidence / avg probability / avg trade quality |
| `GET /api/analytics/health-score?start_date=&end_date=` | Scanner Health Score -- see the "false_negative_rate limitation" note below |

Every REST route is `GET` only -- there is no write path anywhere in
this package.

**Phase 3 -- WebSocket streaming** (poll-and-diff; see "Phase 3
design decision" below):

| Endpoint | What it does |
|---|---|
| `WS /ws/dashboard` | On connect: sends one full current snapshot (`scanner_status`, `system_health`, `new_signals` if any trades exist today, `replay_progress` if replay analytics is enabled). After that: sends one message per event, only when something actually changed since the last poll cycle. |

Message shape: `{"type": "<event_type>", "data": {...}}`. Event types:

| `type` | Sent when | Source (same call the REST route makes) |
|---|---|---|
| `scanner_status` | today's scanner status dict changes | `ScannerService.get_status()` |
| `new_signals` | a trade_id appears that wasn't in the previous cycle | `ScannerService.get_trades_by_id()` |
| `trade_update` | an existing trade_id's `status` changes | same |
| `system_health` | the health dict changes | `HealthService.get_health()` |
| `replay_progress` | journal entry count or daily/weekly/monthly summary availability changes (only sent if `REPLAY_ANALYTICS_ENABLED`) | `ReplayService` |

**Not sent, on purpose:** `analytics_progress`. `AnalyticsService`
computes on demand, synchronously, per REST request -- there's no
background job with persisted incremental state to report progress
on. See `services/live_service.py`'s module docstring for the full
reasoning; this is the same "don't fabricate a plausible-looking
number" rule the rest of this project follows.

## Phase 3 design decision: poll-and-diff, not publish-on-write

The Part 8 spec offered two ways to implement live streaming:

  (a) **poll-and-diff** -- the dashboard API re-pulls the state branch
      (and the replay_analytics daily_cache) on its existing refresh
      interval and pushes only what changed to connected clients. No
      changes to `main.py`.
  (b) **publish-on-write** -- `main.py`'s run loop pushes events at
      the moment they happen. Requires touching the trading job's
      code, which crosses the "don't modify existing modules"
      constraint from Part 8 and needs to be a deliberate,
      explicitly-approved exception.

**(a) was chosen.** It keeps the same architecture as Phases 1-2 (a
fully separate, decoupled, read-only process) and needs no exception
to the "don't modify existing modules" rule. The tradeoff, stated
plainly: updates are only as fresh as `DASHBOARD_WS_BROADCAST_INTERVAL_SECONDS`
(default: same as `DASHBOARD_STATE_REFRESH_SECONDS`, 15s) -- this is
not sub-second push notification, it's the existing polling model with
a WebSocket instead of the client re-polling REST endpoints itself.
If (b) is ever wanted instead, it is a separate, explicit change to
`main.py` that needs its own review -- not something to fold into this
package silently.

### Known limitations (Phase 3)

- **Single process, single worker.** `ConnectionManager`
  (`api/ws_manager.py`) and the poll loop (`services/live_service.py`)
  are in-memory and per-process. Running `run_dashboard_api.py` with
  multiple uvicorn workers would give each worker its own connections
  and its own poll loop -- a client is only notified by whichever
  worker it's connected to. Fine for the current single-worker run;
  would need a shared pub/sub layer (e.g. Redis) to fix for multi-worker.
- **No reconnect logic here.** Reconnect is a client-side concern per
  the spec, and there's no Phase 4 client yet to wire it into. Any
  client should treat a close/error as a signal to reconnect with
  backoff -- it will get a fresh full snapshot immediately on the new
  connection.
- **No auth on `/ws/dashboard`.** Same posture as every REST route
  today -- see "Auth/rate limiting" below (Phase 8).

## Part 10 -- fault tolerance & error handling (previous pass)

Scope of that pass: **Fault Tolerance + Error Handling** for the
Phase 3 poll-and-diff pipeline only.

**What was wrong before:** `live_service.compute_diff_events()` read
four independent sources (scanner status, system health, trades,
replay progress) with no isolation between them. An exception in any
one source (e.g. a transient git-pull failure surfacing inside
`replay_service`) aborted the whole poll cycle before the other three
ran, so a healthy scanner-status update could silently never reach
connected clients just because an unrelated source hiccuped that
tick. `poll_loop()`'s existing `except Exception: log.exception(...)`
only caught this after the fact, once per whole cycle, with no
per-source detail and no way to recover a partial result.

**Files added/changed and why:**

- **`services/error_handling.py` (new).** `safe_call()` runs one
  source's read, catches any exception, and returns a structured
  error record (timestamp, component, module, method, exception type,
  message, stack trace, suggested resolution, recovery action) instead
  of raising. `ComponentFailureTracker` is a thread-safe consecutive-
  failure counter per component. Neither is imported by anything
  outside `app/backend/` -- no effect on `main.py`, scanner, or broker
  code.
- **`services/live_service.py` (modified -- necessary, kept minimal).**
  `compute_diff_events()` and `build_full_snapshot()` now run each of
  their four sources through `safe_call()` independently: a failing
  source is skipped for that cycle (its cached value is left
  untouched so the next successful cycle still diffs correctly) while
  the other three are still evaluated and broadcast. `poll_loop()`
  gains graceful interval backoff -- it only widens its sleep interval
  (capped at 4x, and only once a single component has failed 3+ cycles
  in a row) and narrows back to the configured interval automatically
  the moment that component recovers. The message schema clients
  receive on `/ws/dashboard` is unchanged; no event type was added,
  removed, or renamed.
- **`tests/test_phase3_live.py` (extended, nothing removed).** Two new
  tests: one source failing doesn't block the other three; a source
  that fails once and then recovers diffs correctly against its last
  real value (not corrupted by the failed cycle in between).

## Part 10 -- health monitoring & watchdog (this pass)

Scope of this pass: **Health Monitoring + Watchdog**, again scoped to
the Phase 3 poll-and-diff pipeline (the only part of the app this
project has touched so far).

**What was wrong before:** `error_handling.py`'s per-component failure
counts existed but were only ever logged -- nothing read them, so an
operator watching `/api/health` (the one health signal that already
exists) would have no idea the live pipeline was struggling. Separately,
`poll_loop()` awaited `asyncio.to_thread(compute_diff_events)` with no
timeout: a genuinely hung git operation inside `state_reader` (network
half-open, waiting on a credential prompt that will never come) would
block that await forever, and nothing in the app would ever notice the
background task had frozen.

**Files added/changed and why:**

- **`services/watchdog.py` (new).** `CycleWatchdog` records when each
  poll cycle starts/finishes, how long it took, and how many times a
  cycle has timed out. `check()` reports "frozen" only once the loop
  has gone significantly longer (default 4x) than its currently
  effective poll interval without completing a single cycle -- so
  ordinary backoff (from the previous pass) is never mistaken for a
  freeze, and a process that simply hasn't run its first cycle yet
  (e.g. right after startup, or in a test) is correctly reported as
  "unknown", not "frozen".
- **`services/live_service.py` (modified -- necessary, kept minimal).**
  `poll_loop()` now wraps its `asyncio.to_thread(...)` call in
  `asyncio.wait_for(..., timeout=settings.WS_CYCLE_TIMEOUT_SECONDS)`.
  On timeout, the cycle is abandoned (the underlying OS thread can't
  be force-killed, so it finishes on its own in the background and its
  result is discarded), a CRITICAL log line is written, and the loop
  continues on its normal cadence instead of hanging forever. Two new
  getters, `get_failure_status()` and `get_watchdog_status()`, expose
  this state for `health_service.py` to read.
- **`settings.py` (modified -- one new setting).**
  `DASHBOARD_WS_CYCLE_TIMEOUT_SECONDS` (default 30s) controls the
  timeout above. Optional, same pattern as every other setting in this
  file; a missing/invalid value falls back to the default rather than
  crashing startup.
- **`services/health_service.py` (modified -- necessary, additive
  only).** `get_health()` gained a third source: the Phase 3
  pipeline's own `get_failure_status()`/`get_watchdog_status()`,
  surfaced under a new `"live_pipeline"` response key. Two new issue
  categories can now appear in the existing `"issues"` list -- "live
  update pipeline appears frozen" and "live update pipeline components
  failing repeatedly" (3+ consecutive failures) -- each still docking
  the score by the same fixed 20 points as every other issue category,
  so the scoring model is unchanged. `live_service` is imported lazily
  *inside* `_live_pipeline_health()`, not at module top, because
  `live_service` already imports `health_service` at module level;
  importing it back at load time would create a circular import. The
  whole read is wrapped in `error_handling.safe_call()` (reusing the
  previous pass's utility rather than writing a second try/except
  pattern) so a problem reading live_service's state can never itself
  break `/api/health`. All existing response keys (`status`, `score`,
  `issues`, `state_branch`, `dashboard_process`) are unchanged in
  shape; `"live_pipeline"` is purely additive.
- **`tests/test_watchdog.py` (new).** Five unit tests for
  `CycleWatchdog` in isolation (no dependency on the rest of the repo,
  since `watchdog.py` only uses `threading`/`time`).
- **`tests/test_backend_services.py` (extended, nothing removed).**
  Two new tests confirm `get_health()` surfaces a frozen pipeline and a
  repeatedly-failing component as issues, plus the existing
  "healthy when nothing is wrong" test now also asserts the new
  `live_pipeline` key looks correct by default (not frozen, and
  honestly reports "no cycle has run yet" rather than treating that as
  a fault).

**Verification note (read before trusting a green run):** this repo
pass was checked with `pytest` against minimal hand-written stand-ins
for `state.py`, `config.py`, `daily_cache.py`, and
`app/engines/replay_analytics/*` -- none of which ship in this zip --
because those are the real repo's files, not this dashboard add-on's.
Against those stubs: all `test_watchdog.py`, `test_backend_services.py`,
and the two new fault-isolation tests in `test_phase3_live.py` passed.
One **pre-existing** failure was found and is *not* caused by this
pass -- `test_compute_diff_events_reports_trade_status_change` also
fails against the original, unmodified `live_service.py` when run with
the same stub, because the test fixture mutates a trade dict in place
and `get_trades_by_id()`'s shallow copy shares the same nested trade
objects, so the "before" and "after" snapshots end up aliased to the
same object. Worth a look, but out of scope for this pass and not
something introduced by it. Re-run the full suite against your real
repo (not these stubs) to confirm both the pre-existing issue and this
pass's own tests before deploying.

## What's genuinely implemented vs. deliberately left out

**Implemented for real**, run against the actual `state.py` schema and
the actual `config.py` module (see `tests/test_backend_services.py`):
`ScannerService`, `HealthService`, `ConfigurationService`,
`DashboardService`, and the Phase 1 REST routes above.

**Phase 2, also implemented for real** (see
`tests/test_phase2_analytics_replay.py`, run against fixtures shaped
like `trade_journal.build_journal_entry()`'s actual output):
`ReplayService`, `AnalyticsService`. Two honest limitations worth
knowing about before relying on these:

1. **Daily/weekly/monthly summaries only show what the replay job has
   already generated and persisted.** This dashboard process
   deliberately does NOT call `periodic_reports.generate_daily/
   weekly/monthly_summary()` itself, because those functions both
   compute *and write* a report -- calling them from a read-only
   process would create a report file from whatever partial journal
   data happens to exist at request time, which could quietly become
   the "official" report for that period. If the real scheduled
   replay job hasn't produced one yet, the endpoint returns
   `available: false` rather than generating one on the spot.
2. **`/api/analytics/health-score`'s `false_negative_rate` is always
   0.0.** The real health score needs rejected-candidate and missed-
   opportunity data that only exists inside the offline replay job's
   own run (it has candle data to evaluate rejected setups; this
   dashboard process does not). The endpoint says this explicitly in
   its own `limitation` field, not just here.

**Phase 3, also implemented for real** (see
`tests/test_phase3_live.py`): the poll-and-diff loop
(`services/live_service.py`), the `ConnectionManager`
(`api/ws_manager.py`), and the `/ws/dashboard` route
(`api/routes/live.py`). See "Phase 3 design decision" above for why
poll-and-diff was chosen over touching `main.py`, and the event-type
table for exactly what gets pushed (and what's deliberately not --
`analytics_progress`).

**Deliberately NOT implemented yet** (not stubbed with fake
data -- just absent, to keep this reviewable and honest):

- **React dashboard UI** -- these phases are API-only.
- **Export (CSV/JSON/Excel/PDF), search, filters** -- not built yet.
- **Configuration reload/validate/export/import** -- `config.py` is
  committed source, not a runtime-mutable store; "hot reload without
  restart" needs a different config storage design to do honestly,
  not a fake reload button.
- **API latency / cache-hit-ratio / queue-size metrics** -- there is
  no persistent cache or queue in the existing app to measure yet.
  `/api/health` reports only what's real: state-branch reachability
  and the dashboard process's own resource use.
- **Notification service integration, alert monitor stats** (Total/
  Accepted/Rejected Alerts, High/Medium/Low priority, alert success
  rate) -- "priority" and "accepted/rejected" aren't real fields
  anywhere in the current alert pipeline, so this needs that gap
  addressed first rather than a fabricated breakdown.
- **Auth/rate limiting on this API itself** -- CORS is wide open
  (`allow_origins=["*"]`) and there's no auth layer. Fine for
  localhost/private-network use; do not expose this publicly as-is.
- **Full test/security/performance test suite, deployment guide,
  troubleshooting guide** -- only unit tests for the six services
  built so far exist (`tests/test_backend_services.py`,
  `tests/test_phase2_analytics_replay.py`).

## Tests

```
pip install pytest
cd scripts && pytest app/backend/tests/ -v
```

`test_backend_services.py` (Phase 1) monkeypatches `state_reader` with
fixture state shaped like the real schema (`main.py:new_trade_record`,
`state.py:_fresh_state`). `test_phase2_analytics_replay.py` (Phase 2)
monkeypatches `trade_journal`/`daily_cache` with fixtures shaped like
`trade_journal.build_journal_entry()`'s real output.
`test_phase3_live.py` (Phase 3) unit-tests the diff logic in
`live_service.py` directly (new signal detection, trade status-change
detection, health-change detection, and that `analytics_progress` is
never emitted), plus one integration test that opens a real
`/ws/dashboard` connection via Starlette's `TestClient` and asserts
the initial full snapshot arrives. None of the three files need
git/network access to run.

Note on this sandbox: these tests were written and syntax-checked
(`python -m py_compile`) but not executed here -- only `app/backend/`
and `run_dashboard_api.py` were uploaded, not the rest of the repo
(`state.py`, `config.py`, `daily_cache.py`,
`app/engines/replay_analytics/`) that these modules import, and this
environment has no network access to install `fastapi`/`uvicorn`/etc.
Run `pytest app/backend/tests/ -v` in your actual repo checkout to
confirm.
