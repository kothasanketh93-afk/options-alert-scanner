# Early Breakout Engine

Pilot module for the enterprise-structure migration (2026-07-25) — see
`scripts/app/__init__.py` for why this one module was migrated instead of
the whole repo at once.

## Purpose

Catch an options premium move at its Stage 1/2 origin (institutional
accumulation → base formation → early breakout) instead of scoring a
strike's *current* state, which — confirmed live — tends to alert only
after most of the move has already happened.

## Responsibilities

- Read underlying (NIFTY/BANKNIFTY/MIDCPNIFTY/SENSEX + configured stocks)
  15-min candles and classify trend/institutional-accumulation/base
  formation/breakout/follow-through, in that order, per symbol+direction.
- Select an ATM option leg only after the underlying independently confirms
  (never scores an option leg to decide direction).
- Produce a fully-formed, `main.new_trade_record()`-compatible trade setup
  for anything that clears every stage's gate.
- **Not** responsible for: sending Telegram messages (`telegram_notify.py`),
  persisting/tracking open trades (`state.py` / `tracker.py`), or broker
  auth/rate-limiting (`angelone.py` / `upstox.py` / `retry_utils.py`) — all
  reused as-is, not duplicated here.

## Inputs

- `oi_history: dict` — shared OI-tracking dict, passed in by the caller
  (`main.py`) and mutated in place (same dict the original Trade Score
  Engine uses, so OI history is shared across both engines, not duplicated).
- Implicitly: `config.SYMBOLS`, live candles/option-chain data via
  `market_data.py` / `option_chain.py` (both reused, not reimplemented).

## Outputs

- `detect_all_early_breakout_setups(oi_history) -> list[TradeSetup]` —
  best-first list of qualifying setups (see `models.py` for the exact
  shape). Empty list is a valid, expected output (no session, no
  qualifying setup this cycle — not an error).
- `get_last_scan_stats() -> ScanStats` — candidate/rejection counts from
  the most recent call, for logging/observability.

## Configuration

Every threshold is in `scripts/config.py`, prefixed `EARLY_BREAKOUT_` /
`BASE_` / `INSTITUTIONAL_` / `MOMENTUM_` / `FRESH_BREAKOUT_` /
`EARLY_ENTRY_` / `EXTENDED_MOVE_` / `PARABOLIC_` / `SMART_MONEY_`. None of
it is duplicated here — deliberately: this engine reads the same central
`config` module every other part of the app does, rather than introducing
a parallel `config.py` inside this folder that could drift out of sync
with it. (This is a conscious deviation from the enterprise brief's literal
per-capability `config.py` example — see the note in `app/__init__.py`.)

## Validation

Each stage is a hard gate — a symbol+direction that fails one stops there,
it isn't scored further:
1. Institutional Score ≥ `INSTITUTIONAL_SCORE_MIN`
2. Base Score ≥ `BASE_SCORE_MIN`
3. Stage 4 breakout conditions (price + volume + VWAP + EMA alignment) all pass
4. Stage 5 follow-through (structure + volume) confirms
5. Liquidity hard filter (`filters.liquidity_ok`) — never softened
6. Extended-move filter (premium/ATR/parabolic) — reject if already run too far
7. Minimum 1:2 risk:reward
8. Composite score ≥ 60 (below that: rejected outright, not even Watchlist)

## Processing Flow

```
for each symbol in config.SYMBOLS, for each direction in (CE, PE):
    resolve underlying instrument → fetch candles
    Stage 1  underlying trend (breakout_score.trend_strength)
    Stage 2  institutional_score.compute_institutional_score()  → gate
    Stage 3  base_detector.compute_base_score()                 → gate
    Stage 4  breakout_detector.detect_early_breakout()           → gate
             (age-aware: finds the FIRST breakout candle within
             FRESH_BREAKOUT_MAX_AGE, not a later continuation candle)
    Stage 5  breakout_detector.confirm_follow_through()          → gate
             fair_value_gap.fvg_confirmation()  → non-gating bonus (2026-07-25)
    select ATM option leg → liquidity gate
    breakout_detector.extended_move_filter()                     → gate
    momentum.momentum_strength(), smart_money_score.compute_smart_money_score()
    levels.dynamic_trade_levels() → risk:reward gate
    breakout_score.total_score() (Fresh Breakout Score decay + FVG bonus applied) → grade
sort candidates best-first, return
```

## Scope note (2026-07-25)

A follow-up request asked for a full "Institutional Intelligence Engine" —
15 sub-engines (Smart Money, Accumulation/Distribution, Volume Profile,
Order Flow, Liquidity Zones, Break of Structure/CHoCH, Supply/Demand,
Order Blocks, Fair Value Gaps, Price Action patterns, Institutional Volume,
VWAP Intelligence, Delivery Analysis, Smart Money Confirmation,
Institutional Scoring). Only **Fair Value Gap** (`fair_value_gap.py`) was
built from that list, folded into this existing engine rather than a new
parallel one. Reasoning:
- Institutional Score and Smart Money Score already existed here —
  re-building them under a new name would duplicate, not add.
- Order Flow / Delta Volume / Delivery Analysis need L2 tick data or
  delivery percentages that neither broker integration provides (same
  constraint documented in `smart_money_score.py`).
- FVG has the single crispest, most unambiguous definition of everything
  requested (a specific 3-candle pattern) — the rest (order blocks,
  BOS/CHoCH, liquidity sweeps) have several competing informal
  definitions depending on the source, meaningfully higher risk of a
  subtly-wrong implementation if built quickly.

## Scope note (2026-07-26)

A further follow-up ("Trading Intelligence Engine", Part 4) asked for
~13 more sub-engines — Multi-Timeframe Analysis, Trend/Breakout/
Breakdown/Base Formation engines, full chart Pattern Recognition, Price
Action, Support & Resistance Validation, Momentum/Volume Confirmation,
Entry Quality, Signal Validation, Trading Score/Filter. Most of these
already exist in this codebase under different names (Trend →
`breakout_score.trend_strength()` + `app/market/snapshot.py`'s regime;
Breakout → `breakout_detector.py`; Base Formation →
`base_detector.py`; Momentum/Volume Confirmation → `momentum.py` +
existing volume checks; Trading Score/Filter → this very
`breakout_score.py`). Multi-Timeframe Analysis across 7 timeframes was
asked for again too (same as Part 2's Market Intelligence request) and
still needs new broker API calls this pass continues to avoid.

Only **Price Action** (`price_action.py`) — candlestick patterns (Doji,
Hammer, Shooting Star, Marubozu, Bullish/Bearish Engulfing, Inside/
Outside Bar, Momentum Candle) — was built from this list, for the same
reason FVG was: it was asked for TWICE (Part 3 and Part 4, itself a
signal it matters), and every pattern has a standard, textbook geometric
definition. Full chart patterns (Head & Shoulders, Cup & Handle, Double
Top/Bottom, triangles, wedges, channels) were NOT built — they need
multi-swing-point detection with real ambiguity in where a pattern
"starts," the same higher-risk-of-getting-it-subtly-wrong category as
order blocks/BOS-CHoCH from the previous pass.
  subtly-wrong implementation if built quickly.

## Error Handling

`engine.detect_all_early_breakout_setups()` wraps each symbol+direction in
its own try/except — one symbol's exception is logged
(`EB_SYMBOL_FAILED`) and skipped, never aborts the whole scan. The caller
(`main.run_early_breakout_step()`) wraps the *entire* call in its own
try/except too, so a bug in this engine can never block the original Trade
Score Engine's tracker step or daily summary.

## Logging

`logging_setup.get_logger("early_breakout")` — rejections are logged with
their specific reason (`EB_REJECTED {symbol} {direction}: <reason>`), not
a generic "no signal". See `PRODUCTION_FIXES.md` (repo root) and
`INSTITUTIONAL_UPGRADE_CHANGES.md` for the reasoning behind specific
rejection-reason wording choices.

## Performance Strategy

- Reuses `market_data.py`'s existing per-run candle/option-chain caching —
  no new caching layer introduced.
- No new broker calls beyond what `option_chain.py` / `market_data.py`
  already make for a symbol+direction lookup.
- Runs once per scanner cycle, after the original engine, in its own
  try/except (see Error Handling) — does not block or slow down the
  original engine's own step.

## Unit Tests

`tests/test_engine.py` — 24 deterministic, synthetic-data tests, no
broker/network access required:

```
python scripts/app/engines/early_breakout/tests/test_engine.py
```

Covers: base score bounds, pattern detection, higher-low/lower-high pivot
counting, institutional/smart-money score bounds and honest
"unavailable"-factor flagging, first-breakout-not-continuation detection
(a real bug this test caught — see `INSTITUTIONAL_UPGRADE_CHANGES.md`),
Fresh Breakout Score decay, the entry-window math, the parabolic-move
filter, momentum's direction-awareness, FVG detection/fill-status/
noise-floor filtering/scoring-bonus, the EMA50 candle-count regression
(see the scope note below), Market Snapshot banding, and candlestick
Price Action pattern detection/direction-alignment/scoring-bonus.

## Acceptance Criteria

- [x] `python -m py_compile` clean on every file in this package
- [x] `tests/test_engine.py` passes 11/11
- [x] `main.py`'s only reference to this engine is `from app.engines.early_breakout import detect_all_early_breakout_setups, get_last_scan_stats` — no other file reaches into this package's internals
- [x] Zero changes to `config.py`'s existing constants, `angelone.py`,
      `upstox.py`, `auth.py`, `.github/workflows/*`, or the original Trade
      Score Engine (`scanner.py`)
- [x] Old `scripts/strategy/` package removed only after the above were
      verified, not before
