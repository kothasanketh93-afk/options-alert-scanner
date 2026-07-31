# Trade Lifecycle — Implementation Report

Alerts-only. No order placement, no broker position writes anywhere in
`trade_lifecycle.py`. It never sets `trade["status"]` — that remains
`tracker.py`'s / `btst_tracker.py`'s job exclusively; this module only
reads it and adds a parallel display label + metrics.

## Modified files

| File | Change |
|---|---|
| `scripts/trade_lifecycle.py` | **New file.** Status-vocabulary mapping, per-cycle metrics (current price/PnL/max profit/max drawdown/holding time), EOD "OPEN AT EOD" labeling, dashboard-record view. |
| `scripts/config.py` | **Additive only.** One new constant, `EOD_STATUS_CHECK_HHMM = (15, 25)`. No existing value changed. |
| `scripts/main.py` | **Additive only**, 3 small insertions: import `trade_lifecycle`; call `update_lifecycle_metrics()` right after `tracker.process_trade_candles()` inside the existing `run_tracker_step()` loop (reuses candles already fetched that cycle — zero extra broker calls); call `mark_open_at_eod()` once per run, gated by `now >= EOD_STATUS_CHECK_HHMM`, before the existing daily-summary step. No existing logic removed or rewritten. |
| `scripts/btst_main.py` | **Additive only**, mirrors the `main.py` change inside `run_exit_step()`'s existing loop — one `update_lifecycle_metrics()` call after `btst_tracker.process_position_candles()`. No existing BTST exit logic touched. |

`tracker.py`, `btst_tracker.py`, `state.py`, `btst_state.py` — **not modified**. Every actual target/SL/EOD/force-exit decision still comes from exactly the same code as before.

## Status vocabulary → internal status mapping

| Lifecycle status | Derived from |
|---|---|
| `OPEN` | `WAITING_ENTRY` / `ACTIVE` / `T1_HIT` (still tracking toward T2), before the EOD flag is set |
| `TARGET HIT` | `T2_HIT` (intraday) or BTST `T2_HIT` |
| `SL HIT` | `SL_HIT` (intraday) or BTST `SL_HIT` / `GAP_SL_HIT` |
| `OPEN AT EOD` | Still in a live status when `mark_open_at_eod()` runs (~15:25) — label only, `trade["status"]` unchanged |
| `CLOSED` | `EOD_CLOSED` / `AMBIGUOUS` (intraday), or BTST `FORCE_EXIT` / `AMBIGUOUS` |
| `BTST` | BTST position still `OPEN` overnight |
| `STBT` | Same, for a `strategy == "stbt"` position — vocabulary support added now; no STBT scanning engine exists in this codebase yet, so this only activates if/when one sets that strategy tag on a trade |
| `EXPIRED` | Still live but the option's own `expiry` date has passed (orphaned trade) |
| `CANCELLED` | `NOT_ENTERED` (entry zone never touched), or an explicit `trade["cancelled"]` flag if one is ever set upstream |

## Storage approach

**No new storage.** Every field this module writes (`current_price`,
`current_pnl_pct`, `maximum_profit_pct`, `maximum_drawdown_pct`,
`holding_time_minutes`, `_marked_open_at_eod`) is just additional keys
on the *same* trade/position dict `state.py`'s `save_state()` and
`btst_state.py`'s `save()` already git-commit to the `state` branch
every run (`state/<date>.json`, `state/btst/<date>.json`). Since both
are plain `json.dump()` of a plain dict, new keys round-trip for free —
no schema migration, no new file, no database. Trades survive a GitHub
Actions runner restart exactly as they already did, with the lifecycle
fields riding along.

## Performance impact

- `update_lifecycle_metrics()`: O(1) per trade per cycle, pure
  arithmetic on data already in memory (no I/O, no broker call — it
  reuses the candle close `run_tracker_step()`/`run_exit_step()`
  already fetched for tracking).
- `mark_open_at_eod()`: one O(n) pass over `state["trades"]` (n = open
  trades that day, typically single digits to low tens), once per run,
  only after 15:25 — negligible.
- No new network calls, no new rate-limit exposure (see prior Rate
  Limit Investigation), no new git branch/commit.

## Backward compatibility

- Existing consumers of `trade["status"]` / `trade["final_status"]`
  (Telegram formatters, daily summary, streak tracking, dashboard
  backend) are untouched — that field's values and meaning are
  unchanged.
- New keys are additive; any code that doesn't know about them simply
  ignores them (plain dict, no dataclass/strict schema anywhere in this
  codebase).
- `to_dashboard_record()` is opt-in — nothing calls it automatically
  yet, so it has zero effect until a dashboard/API layer chooses to use
  it for the Trade-ID/Entry/SL/Targets/... display shape requested.
- If `EOD_STATUS_CHECK_HHMM` or `trade_lifecycle.py` were removed
  entirely, `main.py`/`btst_main.py` would need their added lines
  removed too, but nothing else in the system depends on them —
  clean, isolated addition.
