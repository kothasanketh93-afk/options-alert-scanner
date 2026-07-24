# Production Enhancement — Adaptive Directional Bias

## TL;DR

Only **one function** was the actual cause of the reported symptoms. It has been fixed.
Everything else you asked for (probability always calculated, watchlist mode, probability
evolution tracking, rejection analytics, broker rate-limit protection with retry +
failover, candle caching) **already exists in this codebase** — it just never got a
chance to run, because `option_chain.directional_bias()` was silently returning `[]`
and short-circuiting the whole pipeline before any of it executed.

## Root cause

`scripts/option_chain.py`, `directional_bias()` (called from `scanner.detect_setups()`):

```python
# BEFORE
candles_5m = ...
bias_5m = ema_trend_direction(candles_5m)
if bias_5m is None:
    return []
if not multi_timeframe_confirms(symbol, bias_5m):   # 15-min disagrees
    return []                                        # <-- entire symbol skipped
return [bias_5m]
```

`scanner.detect_setups()` then does:

```python
biases = directional_bias(symbol)
if not biases:
    return []   # no strikes scored, no probability, no alert, no watchlist entry
```

So whenever the 5-min and 15-min EMA trend disagreed (routine in a choppy open or a
pullback inside a trend), the **entire symbol** was dropped before a single strike
was scored — matching exactly problems 1–5 in your report.

## Fix

`directional_bias()` no longer hard-gates on disagreement. It now:

1. Uses the 5-min EMA cross as the primary direction whenever it's readable —
   **regardless of whether 15-min agrees.** Disagreement is logged and left to the
   score, not treated as a reason to stop.
2. Falls back to the 15-min EMA cross if 5-min has too little data yet.
3. If **neither** timeframe has enough candles to read a trend, scores **both CE and
   PE** as "Unknown" bias rather than skipping the symbol.
4. Only returns `[]` if the underlying instrument itself can't be resolved — a
   genuinely missing symbol, not a bias question.

The 15-min disagreement is **not thrown away** — it still costs the candidate real
points. `scanner.score_strike()` already calls `multi_timeframe_confirms()` per
strike and feeds it into `trade_score.htf_strength()`, one of the 10 weighted Trade
Score factors (`config.TRADE_SCORE_WEIGHTS["htf"] = 5`). Routing the penalty through
the *existing* weighted engine — instead of bolting a new point system on top — means
no architecture change, no weight-rebalancing risk, and it stays consistent with how
every other soft signal (volume, OI, momentum, VWAP…) already works in this scanner.

`scanner.py`'s one log line describing the old "no bias" empty-scan case was updated
to describe what actually still causes `[]` today (unresolved instrument), so the logs
don't keep describing an emptied-out condition that no longer happens.

## Why nothing else needed to change

Checked each requested fix against the current code:

| Fix | Status | Where |
|---|---|---|
| 1. Remove hard EMA gate | **Fixed this pass** | `option_chain.directional_bias()` |
| 2. Always calculate probability | Already true once Fix 1 landed | `trade_score.compute_probability_pct()`, called unconditionally in `scanner.score_strike()` for every candidate that clears the 3 hard risk gates |
| 3. Watchlist mode (store, don't alert, escalate later) | Already implemented | `config.PROBABILITY_BANDS` / `TELEGRAM_MIN_PROBABILITY` (80%) gate the **Telegram send only**; `main.run_scanner_step()` tracks every candidate ≥`ENTRY_SCORE` regardless, and re-scores it fresh every run so it naturally escalates to an alert once probability crosses 80% |
| 4. Relax directional bias | **Fixed this pass** (same change as #1) | `option_chain.directional_bias()` |
| 5. Broker rate-limit protection | Already implemented | `market_data.py` caches candles/option-chain per (key, interval) per run; `angelone.py` has a rolling-window historical-call throttle; `retry_utils.py` does exponential backoff + jitter + `Retry-After` honoring; `broker_failover.py` auto-switches Angel One → Upstox after N consecutive failures |
| 6. Rejection analytics | Already implemented | `filters.rejection_reasons()` / `reasons_from_factors()`, logged per strike in `scanner.score_strike()` |
| 7. Better logging | Already implemented | `GATE` / `SCORE` / `REJECTED` / `PROBABILITY` log lines in `scanner.py` |
| 8. Probability evolution | Already implemented | `state.record_probability()` / `state.probability_series()` / `trade_score.probability_trend()`, wired into `main.run_scanner_step()` |
| 9. No empty scans | **Fixed this pass** — this was the thing actually causing empty scans | same root cause as #1 |
| 10. Telegram only ≥80% | Already implemented | `config.TELEGRAM_MIN_PROBABILITY = 80`, checked in `main.run_scanner_step()` |

## Files changed

- `scripts/option_chain.py` — `directional_bias()` rewritten (no other functions touched)
- `scripts/scanner.py` — one stale log line/comment updated in `detect_setups()` to match the new (non-empty) behavior; no logic changes

No architecture changes, no features removed, no other files touched.

## Verified

Ran `directional_bias()` (stubbed candle data, no live API needed) against:
- 5-min & 15-min agree → returns the agreed direction.
- 5-min & 15-min **disagree** → still returns the 5-min direction (previously `[]`).
- No candle data at all on either timeframe → returns `["CE", "PE"]` (previously `[]`).
- 5-min data missing, 15-min available → falls back to 15-min direction.

In all four cases the scanner now reaches `score_strike()`, computes a probability,
and either fires an alert (≥80%) or is tracked as a watchlist candidate (60–79%) —
it never again silently produces zero candidates just because the two EMA timeframes
disagreed.

## Expected impact

- **Alerts**: should increase noticeably during choppy/pullback conditions where
  5m/15m EMA disagreement was previously erasing entire scan cycles. Trending days
  where the two timeframes already agreed should see little change.
- **Missed opportunities**: reduced — a good setup on every other factor (volume, OI,
  liquidity, R:R) is no longer discarded solely because of a momentary EMA disagreement.
- **Angel One API usage**: unchanged by this fix (already minimized via existing
  caching/throttling); this fix doesn't add any new API calls, since `directional_bias()`
  was already fetching both 5-min and 15-min candles before (just discarding the result
  on disagreement).
- **Accuracy**: candidates that previously never got a chance to compete will now
  surface, but at a real, quantified cost — 15-min disagreement still measurably lowers
  their Trade Score via `htf_strength()`, so they're not treated as equal to
  full-agreement setups; they simply aren't zeroed out.
