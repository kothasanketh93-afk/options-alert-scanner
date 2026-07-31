# Part 12 — Production-Safe Feature Merge Report

`part3.zip` (Primary Repository) = source of truth. `part12_btst_stbt_recommendation.zip`
(Feature Package) = advisory-only additions merged INTO it. Nothing in
the "do not replace" list was replaced — every one of those files is
either byte-identical to `part3.zip` or has a small, purely-additive
diff (shown below, line by line).

---

## 1. Files Compared

Every file in the Feature Package was diffed against its `part3.zip` counterpart:

| File | In part3.zip? | Diff type |
|---|---|---|
| `scripts/main.py` | Yes | Additive only (see §2) |
| `scripts/config.py` | Yes | Additive only (see §2) |
| `scripts/telegram_notify.py` | Yes | Additive only (see §2) |
| `scripts/btst_main.py` | Yes | **Not merged** — left byte-identical (see §6) |
| `scripts/app/backend/api/app.py` | Yes | Additive only (see §2) |
| `scripts/btst_recommendation.py` | No | New file, added as-is |
| `scripts/lifecycle_analytics.py` | No | New file, added as-is |
| `scripts/app/backend/services/trade_lifecycle_service.py` | No | New file, added as-is |
| `scripts/app/backend/api/routes/trade_lifecycle.py` | No | New file, added as-is |

Also inspected (to confirm no overlap/duplication before merging), all
untouched: `scanner.py`, `market_data.py`, `trade_score.py`,
`app/engines/decision_engine/`, `app/engines/replay_analytics/`,
`app/engines/options_intelligence/`, `app/engines/quant_intelligence/`,
`app/engines/news_intelligence/`, `broker_failover.py`, `retry_utils.py`,
`trade_lifecycle.py` (the existing Part-11-era file), `tracker.py`,
`state.py`, `btst_state.py`, `btst_tracker.py`, `app/backend/api/routes/dashboard.py`,
`app/backend/services/dashboard_service.py`, `app/backend/api/routes/analytics.py`.

## 2. Files Merged

**`scripts/config.py`** — append-only, 0 lines removed from `part3.zip`:
```diff
+ BTST_RECOMMENDATION_ENABLED = False        # wired, functional
+ TRADE_LIFECYCLE_ENABLED = False            # declared, NOT yet wired -- see §7 Conflict #1
+ EOD_TRACKING_ENABLED = False               # declared, NOT yet wired -- see §7 Conflict #1
+ BTST_RECOMMENDATION_MIN_SCORE = 65
+ BTST_RECOMMENDATION_MAX_SPREAD_PCT = 3.0
+ BTST_RECOMMENDATION_MIN_RR = 1.5
+ BTST_RECOMMENDATION_MIN_DTE = 2
```

**`scripts/main.py`** — append-only, 0 lines removed:
- `import btst_recommendation` added next to the existing `import trade_lifecycle`.
- New function `run_btst_recommendations(state, now)` added (self-gated on `BTST_RECOMMENDATION_ENABLED`; returns immediately, no-op, if the flag is off).
- One new line added *after* the existing, **untouched** `trade_lifecycle.mark_open_at_eod(state, now)` call: `run_btst_recommendations(state, now)`.
- The existing `if (now.hour, now.minute) >= EOD_STATUS_CHECK_HHMM:` line and the existing `trade_lifecycle.update_lifecycle_metrics(...)` call inside `run_tracker_step()` are **completely unmodified** — see §6/§7.

**`scripts/telegram_notify.py`** — append-only, 0 lines removed:
- One new function, `fmt_open_at_eod_alert(trade, recommendation)`, appended at end of file. No existing formatter touched.
- (Found + fixed during merge testing: an eager-evaluation bug where `trade.get('display_label', _trade_header(trade))` always called `_trade_header()` even when `display_label` was present. Not a behavior change to any *existing* formatter — the bug was introduced by this new function itself in the Feature Package and is fixed in this merge. See §12.)

**`scripts/app/backend/api/app.py`** — 1 line modified (import tuple extended), 1 line added:
```diff
- health_extended, monitoring, diagnostics,
+ health_extended, monitoring, diagnostics, trade_lifecycle as trade_lifecycle_routes,
...
+ app.include_router(trade_lifecycle_routes.router)
```
No existing router, middleware, or route removed or reordered.

**New files, added verbatim** (no part3.zip counterpart to conflict with):
`scripts/btst_recommendation.py`, `scripts/lifecycle_analytics.py`,
`scripts/app/backend/services/trade_lifecycle_service.py`,
`scripts/app/backend/api/routes/trade_lifecycle.py`.

## 3. Files Left Unchanged

`main.py`'s `run_tracker_step()` lifecycle call, and `btst_main.py` in
its entirety, are left **byte-for-byte identical** to `part3.zip` — see
§6/§7 for why. Every file in the "do not replace" list not mentioned in
§2 (`scanner.py`, `market_data.py`, `trade_score.py`, `decision_engine`
package, `replay_analytics` package, `broker_failover.py`,
`retry_utils.py`, `production/*`, the scheduler, and the pre-existing
dashboard route/service files) is untouched.

## 4. Duplicate Logic Removed

None found to remove — the Feature Package never re-implemented
anything `part3.zip` already had:
- Analytics: `lifecycle_analytics.py` calls
  `app.engines.replay_analytics.performance_analytics.compute_performance()`
  directly; it does not reimplement win-rate/expectancy/drawdown math.
- Telegram: `fmt_open_at_eod_alert()` is additive to the existing
  `telegram_notify.py` service; no second Telegram client/service exists.
- Dashboard: the new `/api/lifecycle/*` routes follow the exact
  same one-route-file-per-feature-area pattern `analytics.py` /
  `replay.py` already use in this app (confirmed by reading
  `dashboard_service.py`'s own docstring, which says new feature areas
  are added as separate services, not folded into `dashboard.py`) — this
  is reuse of the existing app/router, not a second dashboard.
- Trade Lifecycle fields (Trade ID, Status, Duration, P/L, Max
  Profit/Drawdown, Exit Reason, Open At EOD) already exist in
  `part3.zip`'s `trade_lifecycle.py`, untouched by this merge — the
  merge only adds the BTST/STBT layer on top of it.

## 5. New Feature Flags

| Flag | Default | Wired? |
|---|---|---|
| `BTST_RECOMMENDATION_ENABLED` | `False` | **Yes** — gates `btst_recommendation.py` and `main.run_btst_recommendations()` completely |
| `TRADE_LIFECYCLE_ENABLED` | `False` | Declared only — gates the *new* dashboard tabs' visibility, does not gate the pre-existing (already-live) lifecycle tracking calls. See Conflict #1. |
| `EOD_TRACKING_ENABLED` | `False` | Declared only, not yet referenced anywhere. Reserved for the same pending decision as above. |

## 6. Merge Decisions

- **Recommendation engine and its Telegram alert are fully wired and
  opt-in** via `BTST_RECOMMENDATION_ENABLED`. With it `False` (default),
  `run_btst_recommendations()` returns on its first line — zero
  computation, zero Telegram calls, zero dashboard data.
- **`btst_main.py` was left completely untouched.** On inspection, the
  BTST Recommendation Engine only applies to *intraday* trades reaching
  OPEN AT EOD in `main.py`'s flow (deciding whether to start a carry).
  `btst_main.py` tracks positions *already* carried overnight the next
  day (exit-day tracking) — a different, later stage of the lifecycle
  that the spec's "OPEN AT EOD" recommendation doesn't apply to. No
  merge was needed there at all.
- **The two already-live `trade_lifecycle.py` call sites in `main.py`
  (`update_lifecycle_metrics()` in the tracker step, and
  `mark_open_at_eod()` in the EOD check) were deliberately NOT wrapped
  in the new flags.** See Conflict #1 below for the full reasoning —
  in short, they were already unconditional production behavior before
  this merge, and wrapping them in a default-`False` flag would turn
  off something that works today, which the merge instructions
  explicitly forbid ("production stability is the highest priority").
- **The new dashboard tabs (`/api/lifecycle/*`) require an explicit
  opt-in (`TRADE_LIFECYCLE_ENABLED=True`) before returning real data,**
  even though the underlying tracking data is already being collected
  regardless of that flag. This was chosen as the conservative default:
  it makes the *new, additional* dashboard surface opt-in without
  touching whether the *existing* tracking computation runs.

## 7. Conflicts Found — **please read before enabling anything**

### Conflict #1 -- RESOLVED (2026-07-31): Option B chosen

Decision: **Option B.** `TRADE_LIFECYCLE_ENABLED` stays as merged --
it gates only the new `/api/lifecycle/*` dashboard routes' visibility
of already-collected data, and the two pre-existing `trade_lifecycle.py`
call sites in `main.py` / `btst_main.py` remain unconditional, unchanged
from today's production behavior. Rationale: the merge instructions
rank "flags OFF == identical to today's production system" as the
higher-priority requirement, and Option A (wiring the flag to those
call sites) would have silently stopped live Trade ID / Status /
Duration / P&L / Max Profit / Max Drawdown / Open At EOD tracking by
default on next deploy -- an unacceptable regression for a flag
defaulting to `False`.

`EOD_TRACKING_ENABLED` was **removed** rather than left dangling: it
was declared but never imported or checked anywhere (only
`TRADE_LIFECYCLE_ENABLED` gates the dashboard routes), so it did
nothing and only made the intent harder to read. Deleting an
unreferenced flag is a no-op for behavior. `scripts/config.py`'s
comment block above these flags now states this resolution directly,
so a future reader doesn't have to re-derive it from this report.

If tracking itself should ever be killable, that needs a new,
purpose-built flag wired directly to `update_lifecycle_metrics()` /
`mark_open_at_eod()` -- a decision to make deliberately, not by
repurposing `TRADE_LIFECYCLE_ENABLED`.

<details>
<summary>Original conflict writeup (for reference)</summary>

### `TRADE_LIFECYCLE_ENABLED` / `EOD_TRACKING_ENABLED` vs. already-live production behavior

The merge instructions state two things that contradict each other for
these two specific flags:

1. *"Default `TRADE_LIFECYCLE_ENABLED=False` / `EOD_TRACKING_ENABLED=False`."*
2. *"When all feature flags are OFF, application behaviour MUST remain
   identical to today's production system."*

But in `part3.zip` (source of truth), **`trade_lifecycle.update_lifecycle_metrics()`
and `trade_lifecycle.mark_open_at_eod()` are already called unconditionally**
— no flag guards them today. Trade ID / Status / Duration / P/L / Max
Profit / Max Drawdown / Open At EOD tracking is **already running in
production right now**, with no flag to turn it off.

That means:
- If I wire `TRADE_LIFECYCLE_ENABLED`/`EOD_TRACKING_ENABLED` to actually
  gate those two call sites (as the flag names imply they should),
  setting them to their required default of `False` would **disable
  currently-running production functionality** — violating requirement (2).
- If I leave them unwired (what I did), the flags exist and default to
  `False` as required, and requirement (2) holds — but the flags don't
  yet *do* anything for lifecycle tracking, which may not be what's
  wanted from a flag with that name.

**I did not pick a side.** I chose the option that keeps today's
production system unchanged (leaving both call sites unconditional) and
am reporting this rather than silently deciding for you. Two ways to
resolve it, your call:

- **Option A — wire them now.** Wrap both call sites in
  `if config.TRADE_LIFECYCLE_ENABLED:` / `if config.EOD_TRACKING_ENABLED:`
  as their names suggest. **Effect: Trade Lifecycle tracking and OPEN AT
  EOD marking will stop running by default** the next time this is
  deployed, until someone flips the flags to `True`. This is a real
  behavior change to today's production system.
- **Option B — leave as merged.** Keep both call sites unconditional
  (today's behavior, unchanged) and treat these two flags as gating
  only the *new* dashboard visibility (as currently merged). Rename or
  document them more precisely if that's confusing.

</details>

### No other conflicts found.

- No naming collisions: `/api/lifecycle` doesn't collide with any
  existing route prefix (`/api/dashboard`, `/api/analytics`, `/api/replay`, etc.).
- No field-name collisions: `trade["btst_recommendation"]` and
  `trade["_marked_open_at_eod"]` (read, not written, by the new code)
  don't collide with anything `part3.zip` already sets.
- No duplicate Telegram bot/client instantiation.
- No duplicate analytics engine.

## 8. Performance Impact

- `btst_recommendation.evaluate_carry()`: O(1) per trade, pure dict
  reads + arithmetic, no I/O, no broker call, no re-run of any upstream
  engine. Confirmed by code inspection — it only reads
  `trade["why_reasons"]`, `trade["options_intelligence"]`, and
  `trade["decision"]`, all already populated by existing engines this cycle.
- `run_btst_recommendations()`: one O(n) pass over `state["trades"]`
  (n = today's trade count, typically single digits), and only runs
  once past `EOD_STATUS_CHECK_HHMM`; computed once per trade (guarded
  by `"btst_recommendation" in trade`).
- No new network calls, no new broker requests, no new option-chain
  fetch, no new git branch/commit. Zero impact when
  `BTST_RECOMMENDATION_ENABLED=False` (the function returns on its
  first line).
- Dashboard endpoints only run when queried (FastAPI route), and their
  own I/O (`state_reader`, `btst_state.load()`) is data the Trade
  Lifecycle tabs need regardless of how it's fetched.

## 9. Backward Compatibility Verification

Full recursive diff, `part3.zip` vs. merged tree:

```
$ diff -rq part3.zip/... merged/...
Files .../scripts/app/backend/api/app.py differ      (2-line additive diff, see §2)
Files .../scripts/config.py differ                    (append-only, see §2)
Files .../scripts/main.py differ                       (append-only, see §2)
Files .../scripts/telegram_notify.py differ            (append-only, see §2)
Only in merged: scripts/app/backend/api/routes/trade_lifecycle.py   (new)
Only in merged: scripts/app/backend/services/trade_lifecycle_service.py (new)
Only in merged: scripts/btst_recommendation.py                       (new)
Only in merged: scripts/lifecycle_analytics.py                       (new)
```

Every other file byte-identical, confirmed with `diff -rq`. Line-removal
count across the four modified files: **0** lines of pre-existing logic
removed (`app.py` has 1 line *changed*, not removed — an import tuple
extended by one name).

Functional verification performed in this sandbox (no live broker/Telegram
credentials available here, so full production integration test wasn't
possible — see §10 for what still needs to run against real
market/Telegram/state):
- `evaluate_carry()` produces a correct YES/BTST case and a correct
  NO/hard-fail case against synthetic trade dicts using the real field
  names/shapes from `options_intelligence`, `decision_engine`,
  `quant_intelligence`, `news_intelligence`.
- `fmt_open_at_eod_alert()` renders correctly against the merged
  `telegram_notify.py`.
- `lifecycle_analytics.get_lifecycle_analytics()` produces correct
  counts against a synthetic `state` dict, using the real
  `trade_lifecycle.lifecycle_status()` / status-constant names read
  directly from `part3.zip`'s `trade_lifecycle.py` (not assumed).
- `python3 -m py_compile` passed on all 9 touched/new files.

## 10. Manual Testing Checklist (for your environment, not run here — no broker/Telegram credentials in this sandbox)

- [ ] Deploy with all three flags at their default (`False`) and confirm
      one full scan/tracker cycle behaves identically to current
      production (same alerts, same state fields, same Telegram messages).
- [ ] Flip `BTST_RECOMMENDATION_ENABLED=True` only; confirm a recommendation
      is attached to `trade["btst_recommendation"]` only for trades with
      `_marked_open_at_eod == True`, and only ONE Telegram alert per trade.
- [ ] With `BTST_RECOMMENDATION_ENABLED=True`, confirm a trade that hits
      a hard-fail condition (wide spread / HIGH news impact / near expiry)
      always returns `NO` regardless of its weighted score.
- [ ] Confirm `GET /api/lifecycle/analytics` returns `{"enabled": false, ...}`
      while `TRADE_LIFECYCLE_ENABLED=False`, and real numbers once `True`.
- [ ] Confirm existing `/api/dashboard/summary`, `/api/analytics/*`,
      `/api/replay/*` endpoints are unaffected (same response shape,
      same status codes).
- [ ] Restart the process mid-day and confirm `trade["btst_recommendation"]`
      (if already computed) survives via the existing state-persistence
      mechanism, and is not recomputed/re-alerted.
- [ ] Confirm no new outbound HTTP/broker calls appear in logs during a
      cycle with `BTST_RECOMMENDATION_ENABLED=True` vs. `False`.

## 11. Rollback Plan

Because every change is additive:
- **Fastest rollback:** set `BTST_RECOMMENDATION_ENABLED=False` (already
  the default) — returns to today's exact behavior for the new engine.
  No file changes needed.
- **Full rollback:** delete the 4 new files
  (`btst_recommendation.py`, `lifecycle_analytics.py`,
  `app/backend/services/trade_lifecycle_service.py`,
  `app/backend/api/routes/trade_lifecycle.py`), revert the 4 additive
  diffs in `main.py`/`config.py`/`telegram_notify.py`/`app.py` (each is
  a small, clearly-delimited block — see §2), and the repository is
  byte-identical to `part3.zip` again. No database migration, no state
  schema change, no broker-side cleanup required — `trade["btst_recommendation"]`
  is just an extra dict key that any older code already ignores.

## 12. Confirmations

- ✅ **No production feature was broken** — confirmed via full recursive
  diff (§9): 0 lines of pre-existing logic removed across all touched files.
- ✅ **No existing alert generation changed** — `scanner.py`, `trade_score.py`,
  and the alert-formatting functions already in `telegram_notify.py` are untouched.
- ✅ **No trade scoring changed** — `trade_score.py` untouched; the
  recommendation engine runs strictly *after* `trade["decision"]` already exists.
- ✅ **No decision engine behaviour changed** — `app/engines/decision_engine/`
  untouched; the recommendation engine only *reads* `trade["decision"]`.
- ✅ **No auto trading added** — no file in this merge calls a
  broker order-placement function.
- ✅ **No broker positions modified** — no file in this merge imports
  `angelone.py` / `upstox.py` / any broker client.
- ✅ **BTST/STBT remains recommendation only** — `evaluate_carry()` is a
  pure function; it returns a dict and never mutates `trade["status"]`
  or calls anything that could place/close a position.

One bug found and fixed during this merge's testing pass (disclosed in
§2): the Feature Package's `fmt_open_at_eod_alert()` had a Python
default-argument eager-evaluation issue that could raise `KeyError` in
an edge case. Fixed in the merged `telegram_notify.py`; does not affect
any pre-existing formatter.

**Second bug found and fixed after initial merge, via targeted edge-case
testing:** `lifecycle_analytics.compute_lifecycle_counts()` and
`trade_lifecycle_service.get_open_at_eod()` were both counting/listing
by the raw, never-cleared `trade["_marked_open_at_eod"]` flag instead of
`trade_lifecycle.lifecycle_status()`. A trade marked OPEN AT EOD at
~15:25 IST that then genuinely resolves to TARGET HIT / SL HIT before
the real 15:30 close keeps that flag set forever — so the buggy version
double-counted it as still "Open At EOD" *and* dropped it from "Closed
Today" simultaneously. Reproduced with a synthetic trade
(`closed_today` came back `0` instead of `1`, `open_at_eod` came back
`2` instead of `1`), fixed in both files to classify by
`lifecycle_status(t)` (which already correctly resolves this — see
that function's own branching in `trade_lifecycle.py`, unmodified),
and re-verified with the same test case plus a full regression pass.
This affects only the new Analytics/dashboard numbers — no
`trade["status"]` mutation, no alert-generation change, no scoring change.

**Outstanding decision needed from you:** Conflict #1 (§7) —
whether `TRADE_LIFECYCLE_ENABLED`/`EOD_TRACKING_ENABLED` should actually
gate the already-live lifecycle tracking calls (a real behavior change)
or remain reserved for dashboard-visibility only (as currently merged,
zero behavior change). I defaulted to the zero-behavior-change option
and flagged it rather than deciding unilaterally.
