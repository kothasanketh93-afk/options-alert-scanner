# Market Intelligence — Snapshot

Pilot slice of a much larger "Market Intelligence Engine" request (16
sub-engines) — see `snapshot.py`'s module docstring for the full list of
what was and wasn't built, and why.

## Purpose

One shared, per-symbol-per-run object that consolidates indicators that
were already being computed independently (and redundantly) by the Early
Breakout Engine — avoiding the "Repeated Indicator Computations" the
enterprise brief explicitly warns against.

## What it provides

`get_market_snapshot(symbol) -> dict | None`:
- `regime`: `TRENDING_BULLISH` / `TRENDING_BEARISH` / `VOLATILE` / `SIDEWAYS`
  (enhances `indicators.market_regime()`'s existing 3-state read with
  direction, via `ema_trend_direction()`)
- `trend_strength_label`: Very Weak → Very Strong (bands `efficiency_ratio()`)
- `volatility_score` (0-100) / `volatility_level`: Low / Normal / High
- `gap`: session-open vs. previous-day-close gap direction/size, and
  whether it's already filled
- `ema9` / `ema20` / `ema21` / `ema50` / `atr` / `vwap`: computed once,
  reused by callers instead of each recomputing their own copy
- `timestamp`

Returns `None` if there isn't enough candle data yet (e.g. right at
market open) — callers should treat that like "no bias available yet,"
not an error.

## A real bug found while building this

Tracing what indicators the Early Breakout Engine already computed (to
avoid recalculating them here) surfaced a genuine, previously-unnoticed
bug: the engine fetched only 32 underlying candles, but
`indicators.compute_ema()` needs at least `period` closes — so `ema50`
was **always `None`**, which made `breakout_score.trend_strength()` (the
"Underlying Trend" factor — weight 20, the single largest factor in the
whole composite score) **always return 0.0**, silently, for every
candidate this engine has ever scored. Fixed by fetching
`EARLY_BREAKOUT_CANDLE_FETCH_COUNT = 60` candles instead of 32 — verified
directly (`compute_ema(list(range(1,61)), 50)` now returns a real value)
and covered by a regression test
(`test_candle_fetch_count_is_enough_for_ema50`) so a future accidental
reduction of that constant fails loudly instead of silently reintroducing
the bug.

## What's NOT here (and why)

See `snapshot.py`'s module docstring — in short: Market Breadth (no
broad-market data source exists here), Multi-Timeframe analysis across
7 timeframes (would mean new broker API calls this scanner doesn't
currently make — directly against this session's own earlier rate-limit
work), Liquidity depth (no L2 order-book data from either broker), and
Market Confidence/Filter as separate gates (would duplicate gates that
already exist in `institutional_score.py` / `breakout_score.py`).

## Wired into

`app.engines.early_breakout.engine` only, in this pass. **Not** wired
into `scanner.py` (the original Trade Score Engine) — that's a
deliberately separate, more carefully reviewed step given it's the
already-stable, live engine.

## Performance

Module-level dict cache, keyed by symbol, that lives for one process's
run. This scanner runs as a fresh process per scan cycle (GitHub Actions
cron), so this needs no TTL or explicit invalidation — the next cycle
starts with a clean cache automatically.

## Testing

Pure banding functions (`_trend_strength_label`, `_volatility_level`) are
covered in `app/engines/early_breakout/tests/test_engine.py` (no separate
test file here — see that file's `test_market_snapshot_*` tests). The
module deliberately defers its `market_data`/`option_chain` imports to
inside `get_market_snapshot()` so it — and its pure logic — stay
importable and testable without broker credentials, same pattern as
`app.engines.early_breakout`'s lazily-loaded `__init__.py`.
