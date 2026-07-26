# Options Intelligence Engine (Part 5)

Consolidated option-chain analysis — Option Chain, PCR, Max Pain, Open
Interest, Greeks, Implied Volatility, Liquidity, Premium, Strike
Selection, Expiry, and Options Momentum — rolled into one Options
Intelligence Score (0-100) plus a confidence read and a warning list,
per (symbol, direction, moneyness, strike).

## Purpose

Give score_strike() a second, independent read on the option CHAIN
itself (not just the one leg it's scoring) — is the wider chain's OI/
PCR/Max-Pain picture actually consistent with this trade, are this
leg's own Greeks/IV/premium composition healthy, and is the strike/
expiry combination structurally sound — surfaced as one object rather
than a dozen separate ad-hoc reads.

## Responsibilities

- Read the FULL option chain (`market_data.get_option_chain()`, already
  fetched and cached by score_strike() for its own leg lookup — no
  second download) to compute chain-wide OI totals, PCR, and Max Pain.
- Score the ALREADY-EXTRACTED leg's Greeks/IV/premium composition —
  does not re-decide delta acceptability (`filters.delta_acceptable()`/
  `expiry_theta_ok()` already do that upstream) or re-run the liquidity
  hard gate (`filters.liquidity_ok()` already does).
- Track short in-process history (this run only) for IV percentile/
  rank/trend and premium/OI/volume momentum.
- Produce the Standard Output Object (`engine.analyze()`'s return
  value) and log every sub-score plus the final accept/reject decision
  and reason.
- **Not** responsible for: the hard quality gate (`filters.py`), the
  Trade Score / Grade / Probability (`trade_score.py`), SL/target
  sizing (`levels.py`), or Telegram formatting (`telegram_notify.py`)
  — all reused as-is, not duplicated here.

## Inputs

`engine.analyze()` takes only what `scanner.score_strike()` already has
in local variables this cycle: `chain` (the full chain list), `leg`
(the extracted CE/PE leg dict), `spot_price`, `expiry`, `oi_history`,
and the already-computed `oi_delta_pct`. No new broker call is made
anywhere in this package.

## Outputs

`engine.analyze(...) -> dict` — the Standard Output Object: `oi_score`,
`pcr_score`, `max_pain_score`, `greeks_score`, `iv_score`,
`liquidity_score`, `premium_score`, `strike_recommendation`,
`expiry_score`, `options_intelligence_score` (0-100), `confidence`
("Confirmed"/"Partial"/"Weak"), `warning_list`, `accepted`,
`rejection_reasons`, `timestamp`, plus extra display detail (total
call/put OI, overall/strike PCR, max pain level, OI build-up label,
risk rating, premium intrinsic/extrinsic breakdown).

## Rollout status — read before enabling the filter

- `config.OPTIONS_INTELLIGENCE_ENABLED = True` — the object is always
  computed and attached to the trade dict as `setup["options_intelligence"]`
  (additive; nothing existing changes).
- `config.OPTIONS_INTELLIGENCE_FILTER_ENABLED = False` (default) — the
  Options Intelligence Score/filter does **not** reject any trade yet.
  Watch a few days of its logged scores (`OPTIONS_INTEL` log lines)
  against actual T1/T2/SL outcomes before flipping this to `True` —
  same "prove it before it can reject a trade" posture this codebase
  already used for the Trade Score Engine's own rollout.
- IV percentile/rank and premium/OI/volume momentum are **per-process
  only** (reset every GitHub Actions run) — cross-run history would
  need `state.py`'s persisted daily state file, deliberately not wired
  up here to avoid reaching into that schema for this pass. Until then,
  a symbol's IV/momentum reads start neutral (0.5) at the top of every
  run rather than carrying yesterday's picture forward.
- `offset_index` (used for the Deep ITM/Deep OTM strike-depth
  distinction) is passed as a fixed `1` from `scanner.score_strike()`
  today, since that function doesn't currently track how many strikes
  from ATM a candidate is (only its moneyness bucket) — thread the
  real offset through `resolve_strike_candidates()` if you want the
  finer Deep ITM/Deep OTM read to be accurate for `config.STRIKE_RANGE > 1`.

## Tests

`tests/test_options_intelligence.py` — plain assert-based, no pytest
dependency, no broker/network access (same convention as
`app/engines/early_breakout/tests/test_engine.py`). Run with:

```
python scripts/app/engines/options_intelligence/tests/test_options_intelligence.py
```
