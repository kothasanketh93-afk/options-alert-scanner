# IV Scale Verification / Rate Limit / Instrument Master — Investigation Report

Scope: investigation + logging only. No thresholds, no `retry_utils.py`, no
`broker_failover.py`, no `config.py` values were changed. Two adapters
(`angelone.py`, `upstox.py`) got a one-line diagnostic log added at the raw
IV extraction point — no normalization logic, no scale conversion, no
existing behavior changed.

---

## 1. IV Scale Findings

**Both brokers return IV as a percentage number (e.g. `16.33` meaning
16.33%), NOT a 0–1 fraction — and this already matches what the rest of
the codebase expects. No normalization is needed in either adapter.**

### AngelOne — `impliedVolatility` (optionGreek endpoint)
- Field: `scripts/angelone.py`, `_option_greeks()`, `row.get("impliedVolatility")`
- Angel One's own SmartAPI docs/forum sample responses return values like
  `"16.330000"`, `"22.190000"`, `"11.540000"` — percentage points, not fractions.
- Confirms the code style (whatever comes back is passed through `_safe_float()`
  untouched) is already correct for this endpoint.

### Upstox — `option_greeks.iv` (the endpoint this codebase actually calls)
- Field: `scripts/upstox.py`, `get_option_chain()`, `side.get("option_greeks").get("iv")`
- This method calls `/v2/option/chain` (Put/Call Option Chain API). Upstox's own
  docs sample for that specific endpoint shows `"iv": 50.78` — percentage points.
- **Important caveat found during this investigation**: Upstox has *other*
  endpoints that return IV as a 0–1 fraction instead — `/v3/market-quote/option-greek`
  (`"iv": 0.33599853515625`) and the market-data-feed websocket (`"iv": 0.127716064453125`).
  This codebase does not currently call either of those, so it isn't affected —
  but if `option_greek` (v3) or the websocket feed is ever wired in later, its `iv`
  field is a **different scale** than `option/chain`'s and would need its own
  `* 100` normalization at that call site. Flagging this now so it isn't
  rediscovered the hard way later.

### Why no adapter change was made
Downstream, `scripts/app/engines/quant_intelligence/probability/scorer.py`
already assumes percentage-point scale: `score_iv()` does `signal.iv / 15`
(i.e. IV=15 → full score). If either adapter were changed to divide by 100
now, that consumer would silently break (every IV score would collapse
toward 0). Since both adapters already return the same scale, and that scale
already matches the one live consumer of `iv`, converting would introduce a
regression rather than fix one.

Separately, `config.py` has `IV_ABNORMAL_LOW_PCT = 0.08` / `IV_ABNORMAL_HIGH_PCT = 1.20`
— these ARE fraction-scale. Per grep, **nothing in the codebase currently reads
these two constants** — they're not wired to any check yet. Per instructions
they were left untouched. Flagging for whoever wires them up later: they'll
need `raw_iv / 100` at the comparison site (not inside the adapter), since the
adapter's raw `iv` field is percentage-scale for both brokers.

### Raw broker IV samples (deliverable #2)
Live values require a live authenticated run (this environment has no broker
credentials/network access to AngelOne or Upstox). Instead of guessing, a
one-line diagnostic log was added at the exact extraction point in each
adapter, gated to fire once per call:

- `angelone.py` → `log.info("IV_SAMPLE angelone name=%s expiry=%s strike=%s type=%s raw_impliedVolatility=%r", ...)`
- `upstox.py` → `log.info("IV_SAMPLE upstox instrument_key=%s strike=%s side=%s raw_iv=%r", ...)`

Next scheduled run's logs (search for `IV_SAMPLE`) will show the real raw
values from both brokers, e.g.:
```
IV_SAMPLE angelone name=NIFTY expiry=07AUG2026 strike=25000.0 type=CE raw_impliedVolatility='14.870000'
IV_SAMPLE upstox instrument_key=NSE_FO|xxxxx strike=25000 side=call raw_iv=15.22
```
Values in the teens/twenties confirm percentage scale (matches the vendor
docs above); a value like `0.15` would indicate a fraction and *would* need
in-adapter normalization at that call site.

---

## 2. Rate Limit Investigation

**Live measurement wasn't run (no broker credentials in this environment),
but this exact investigation was already done live in production and is
documented in the repo itself** — `scripts/market_data.py`'s module docstring
records three separate live incidents:

| Date | What happened |
|---|---|
| 2026-07-21 | AngelOne `getCandleData` hit "Access denied because of exceeding access rate" on the very first call of a fresh process, even under the throttle. |
| 2026-07-23 | Throttle strengthened from fixed-interval-only to a real rolling 60s window (`_throttle_historical_call()`, still in place, unmodified by this task). Believed fixed. |
| 2026-07-28 | Live CI logs: the strengthened throttle did **not** hold — `getCandleData` was rate-limited again on the run's first two consecutive calls. |
| 2026-07-29 | Confirmed via AngelOne's own SmartAPI forum that *other, unrelated* accounts hit the same 403 while staying 3–4x **under** AngelOne's published per-second limit — i.e. a server-side enforcement bug on AngelOne's end, not a client pacing problem. |

**Conclusion: actual observed AngelOne throttling is stricter, and less
predictable, than `ANGELONE_HISTORICAL_MAX_CALLS_PER_MINUTE=100` /
`ANGELONE_HISTORICAL_MIN_INTERVAL_SECONDS=1.0` would suggest.** The gap isn't
this codebase under-throttling — the client-side limiter (100/min, 1s
spacing) is already conservative and was already tightened once (2026-07-23)
without resolving it. The limit-configs weren't touched here, per instructions,
and shouldn't be tightened further either: AngelOne's own forum indicates this
is an account/server-side issue that a stricter client throttle can't reliably
work around.

**Existing operational mitigation (already in place, unchanged by this task):**
Upstox is primary broker, AngelOne is fallback-only (`market_data.py`,
`_broker = FailoverBroker(_upstox, "Upstox", _angelone, "AngelOne")`), decided
specifically because of this history. AngelOne's historical-candle throttle
only matters when a fallover to AngelOne actually happens.

**Coverage gap worth knowing (not a bug, just scope):** `_throttle_historical_call()`
is only invoked before AngelOne's historical-candle calls (3 call sites:
`get_candles`, `get_daily_candles`, ATR-warmup fetch). AngelOne's other
endpoints — option chain, option greeks, LTP, batch quotes, scrip master
download — go through `request_with_retry` with no rate limiter at all, since
`ANGELONE_HISTORICAL_*` is specifically the documented `getCandleData` limit,
not a general account-wide cap. If those other endpoints ever show the same
403 pattern, this is why: there's no client-side throttle written for them.

No changes made to `retry_utils.py` or `broker_failover.py`, per instructions.

---

## 3. Instrument Master Findings

### `verify_stock_symbols.py` / `verify_stock_config.py`
- **Duplication + one stale copy found.** Three copies of `verify_stock_symbols.py`
  exist: `/verify_stock_symbols.py` (root), `/scripts/verify_stock_symbols.py`,
  and `/.github/workflows/verify_stock_symbols.py` (a stray `.py` file sitting
  in the workflows folder — GitHub Actions ignores non-`.yml` files there, so
  this copy is never executed by anything).
  - The **root copy** (the one actually run — see below) imports
    `config.STOCK_SYMBOLS` / `config.STOCK_STRIKE_STEP` live, so it can't drift
    from `config.py`.
  - The **`scripts/` copy** hardcodes its own `STOCK_SYMBOLS = [...]` list
    (line 37) instead of importing from `config.py`. If `config.py`'s list
    changes (which it does dynamically via `refresh_symbols_for_today()`),
    this copy silently checks a stale list. It isn't wired into any workflow
    today, but is a drift risk if someone runs it manually expecting current
    behavior.
  - `verify_stock_config.py` (root) has no workflow calling it at all — only
    `verify_stock_symbols.py` is invoked by `.github/workflows/verify-config.yml`.
    The `.github/workflows/verify_stock_config.py` copy is the same
    never-runs-as-a-`.py`-file situation as above.
- Not modified — flagging only, since the task said document, not fix, unless
  operationally necessary (see below).

### Cron-job refresh status
- `.github/workflows/verify-config.yml` is **`workflow_dispatch`-only** by
  design (no native GitHub `schedule:` trigger) — the file's own comments say
  triggering is intentionally delegated to an **external cron-job.org job**
  hitting the workflow-dispatch API on a quarterly cadence.
- This lives outside the repository (on cron-job.org's own dashboard), so it
  **cannot be confirmed as active from the code alone**. What can be checked
  is whether it's actually firing:
  - GitHub → Actions tab → "Verify Config" workflow → run history. If the
    most recent run is older than ~1 quarter, the external cron-job.org
    trigger has likely stopped firing (job paused/deleted/auth expired on
    cron-job.org's side) — same class of failure as the `options-alert-scanner`
    project has already hit before with cron-job.org triggers per prior work.
  - **Operational fix if stale:** log into cron-job.org, confirm the job
    calling `verify-config.yml`'s workflow-dispatch endpoint is still enabled
    and its GitHub PAT/token hasn't expired (GitHub API auth failures on
    cron-job.org's side fail silently on their end, not visible in GitHub's
    own UI at all) — this is the same failure mode already documented
    elsewhere in this repo for the main scanning cron triggers.

No code changes made in this section — findings/documentation only, per
instructions.
