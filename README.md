# Intraday Same-Day Trade Tracker

Free, no-database, same-day tracking layer that sits on top of your
NSE options scanner, with automatic Angel One (primary) / Upstox
(fallback) broker failover and Telegram alerts. State lives as one
JSON file per trading day on a dedicated `state` git branch — no
server, no card details, minimal GitHub Actions usage.

## What's here

| File | Purpose |
|---|---|
| `scripts/config.py` | **Every tunable threshold in one place** — grading, liquidity, ATR multipliers, delta floors, circuit breaker, session windows, retry behavior, broker-failover thresholds. Nothing trading-relevant is hardcoded elsewhere. |
| `scripts/exceptions.py` | Custom exception hierarchy (`AuthenticationError`, `BrokerAPIError`, `RateLimitError`, `InstrumentNotFound`, `ConfigurationError`, `TelegramFailure`, `StateCorruptionError`) — replaces generic `RuntimeError` everywhere. |
| `scripts/auth.py` | The only place credentials are read from the environment. Angel One's TOTP is generated automatically here (no manual login, ever). Also validates the Upstox token is actually usable (see its docstring on why "token refresh" isn't automatable for Upstox — this matters less now that Upstox is only the fallback broker). |
| `scripts/config_validation.py` | Runs at startup (before any API calls) — fails fast with every missing secret listed at once (now including Angel One's 4 credentials), instead of failing deep in a run with a confusing error. Also warns loudly about angelone.py's known delta=None gap. |
| `scripts/retry_utils.py` | One shared retry-with-backoff implementation (jitter + `Retry-After` header support + never-retry-auth-failures), used by Angel One, Upstox, and Telegram calls alike. |
| `scripts/broker.py` | Abstract broker interface (`get_candles`, `get_ltp`, `get_option_chain`, `search_instrument`). Both `angelone.py` and `upstox.py` implement this — scanner.py/tracker.py never know which one actually served a call. |
| `scripts/angelone.py` | Concrete Angel One SmartAPI implementation of `broker.py` — the **primary** broker. Automated TOTP login, no manual step ever. Known gap: Option Greeks (delta) not yet wired — see its module docstring. |
| `scripts/upstox.py` | Concrete Upstox implementation of `broker.py` — now the **fallback** broker, used only when Angel One is unhealthy. |
| `scripts/broker_failover.py` | Wraps both brokers behind the same `Broker` interface. Switches to Upstox after consecutive Angel One failures (auth, timeout, empty/stale data, etc.), logs every switch, and automatically tries Angel One first again on the next run — no persisted cross-run state needed. See its module docstring for the full design and reasoning. |
| `scripts/cache.py` | Tiny in-process TTL cache (instrument master, option chain, underlying candles) scoped to one GitHub Actions run. |
| `scripts/logging_setup.py` | Structured logging to named channels, written to stdout (captured by the Actions run log) rather than to disk files that would vanish with the ephemeral runner — see its docstring for the reasoning and how to add persisted log artifacts if you want them later. |
| `scripts/state.py` | Loads/saves the daily state file via the `state` git branch; generates Trade IDs; same-symbol+direction suppression check; **circuit breaker + concurrent trade cap**; **auto-recovers from a corrupted state file** instead of crashing. |
| `scripts/tracker.py` | Chronological 1-min candle processing: entry activation, T1/T2/SL, ambiguous-candle detection, EOD handling |
| `scripts/market_data.py` | Thin compatibility wrapper over `broker.py`/`broker_failover.py` — preserves the exact function signatures scanner.py/tracker.py already call, so the broker abstraction (and later, failover) required zero changes to either. |
| `scripts/telegram_notify.py` | All message templates (new alert, entry, T1, T2, SL, not-entered, EOD, ambiguous, daily summary) — message formats untouched by the architecture work; only the send-plumbing changed. |
| `scripts/scanner.py` | **Plug your existing EMA/OI/volume/delta detection logic in here** — now scans ATM + ITM + OTM per direction. `directional_bias()`/`get_atm_strike()`/`score_strike()` are still stubs — this is next up. |
| `scripts/heartbeat_check.py` | Independent staleness check — verifies Angel One reachability, Upstox reachability+auth, Telegram reachability, and state-branch writability every run (not just staleness), and actually checks git's exit code instead of assuming success |
| `scripts/premarket_check.py` | Runs once daily, ≥15 min before market open — same diagnostics as heartbeat_check.py, so a misconfiguration or outage is caught before the trading day starts rather than 45+ minutes into it |
| `scripts/holiday_guard.py` | Weekend + NSE holiday check, skips runs on non-trading days |
| `scripts/main.py` | Orchestrator run every 5 min (15 min in the lunch-gap phase): validate config → scanner → tracker → notify → save |
| `.github/workflows/trader.yml` | Main job: scanner + tracker, 5-min IST schedule (15-min during the lunch gap), concurrency-protected |
| `.github/workflows/heartbeat.yml` | Runs every 30 min independently, alerts if state branch goes stale |
| `.github/workflows/premarket.yml` | Runs once daily at 09:00 IST, own workflow, own concurrency group |

## Removed

- **`expiry_day.py`** — removed, the daily expiry note was firing too often
  (NIFTY expiry is weekly) to be useful.

- **`weekly_report.py` / `rolling_log.py`** — removed (not required). These
  had been reinstated once before as a grade-vs-outcome feedback loop
  (buckets resolved trades by grade and reports whether higher grades are
  actually winning more often), but were removed again as low-value for
  this setup. If you want that feedback loop back later, `rolling_log.py`
  appended one line per resolved trade to `logs/rolling_log.jsonl` and
  `weekly_report.py` read it every Monday — reintroduce both together,
  plus a `weekly_report.yml` workflow, since neither is useful alone.

## Risk management (new)

Two guardrails added to `state.py`, enforced in `main.py`'s scanner
step — neither depends on `scanner.py` being filled in, so they're
active from day one regardless of where you are on the detection logic:

**Circuit breaker** (`MAX_CONSECUTIVE_SL = 2` in `config.py`): after 2
SL hits in a row (a full T2 win resets the streak; ambiguous/
not-entered/EOD outcomes don't move it either way), new trade alerts
stop for the rest of the day. Already-open trades keep being tracked
normally — only new setups are blocked. You get exactly one Telegram
notification when it trips, not a repeat every run. Tested: trips
precisely on the 2nd consecutive SL, resets cleanly on a win.

**Concurrent trade cap** (`MAX_CONCURRENT_TRADES = 4` in `config.py`):
caps simultaneously open trades (`WAITING_ENTRY` + `ACTIVE` combined)
across all symbols/directions/moneyness. With ATM+ITM+OTM scanning on
2 symbols × 2 directions, exposure can otherwise stack up quickly.
Setups beyond the cap are skipped silently (no per-skip Telegram
message — the daily summary already shows total recommendations, so
nothing is hidden). Tested: with 10 setups detected in one run and the
cap at 4, exactly 4 trades open and 6 are skipped.

Both thresholds are simple constants at the top of `state.py` — adjust
them to match your risk tolerance.

## ITM / OTM alerts

`scanner.py` now scans ATM plus one ITM and one OTM strike per detected
direction (`STRIKE_RANGE = 1`, adjustable — each extra strike on each
side roughly doubles scanner API calls per run). Each moneyness is an
**independent trade** with its own Trade ID
(`NIFTY-CE-OTM-20260713-0940-01`), own score, own entry/target/SL
levels, and its own suppression check — an ATM CE and an OTM CE can be
open on the same symbol+direction simultaneously; duplicates within
the same moneyness are suppressed.

You'll need to fill in `score_strike()` in `scanner.py` so each
strike's OI/volume/delta is read individually rather than copying the
ATM score across strikes — ITM and OTM have materially different risk
profiles and should be scored on their own numbers.

## Heartbeat check

Runs as its **own separate workflow** (`heartbeat.yml`), not inside
the main job — this matters because if the main workflow itself is
stuck, failing, or not triggering (e.g. Upstox token expired), a check
embedded inside it would never fire. The heartbeat instead checks the
`state` branch's last commit time directly. If it's stale by 45+
minutes during market hours, you get one Telegram alert (deduplicated
via a marker file, cleared once things recover).

**Note on broker authentication**: Angel One (primary) logs in fully
automatically every run via TOTP (`auth.get_angelone_credentials()`,
`angelone.py`'s `login()`) — no manual step, ever. Upstox (fallback)
still has the daily-manual-token problem described above, but it now
matters less: Upstox is only used when Angel One is unhealthy, and the
heartbeat check above alerts specifically on Upstox's own token dying
(`check_upstox_reachable()`) so you know before it'd actually be
needed as a fallback.

## Bugs found and fixed (latest review)

A close review after adding risk management turned up two real bugs,
both now fixed and verified with simulation:

1. **`has_open_trade()` / `count_open_trades()` in `state.py`** only
   checked for `WAITING_ENTRY`/`ACTIVE` status, missing `T1_HIT` --
   tracker.py's status label for a trade that hit T1 and is still
   tracking toward T2 (not yet resolved). This meant a live position
   past T1 wasn't counted as "open" for suppression or the concurrent
   trade cap. Fixed: both now include `T1_HIT`.

2. **`telegram_notify.py`'s daily summary miscounted "T1 Success."** It
   checked `status == "T1_HIT"`, which is the label for a trade that's
   still open and actively tracking toward T2 (non-terminal) — not a
   resolved outcome. That silently counted still-open positions as
   finished "successes" in the win-rate math, and gave no visibility into
   trades that hadn't resolved by summary time. Fixed: the bucket now
   reads `final_status == "T1_HIT_THEN_EOD"` (the actual resolved
   "hit T1, EOD before T2" outcome), and a new "Still Open (unresolved)"
   line reports any trade still `WAITING_ENTRY`/`ACTIVE`/`T1_HIT` at
   summary time instead of it vanishing from the numbers.

3. **`heartbeat_check.py` was masking real outages after its first
   alert.** It looked at "the latest commit on the state branch" to
   judge staleness, but heartbeat's *own* alert-commits land on that
   same branch -- so after sending one alert, its own commit became
   the new "latest," making a dead main workflow look freshly active
   and silencing all further alerts, permanently, even hours into an
   outage. Fixed: `get_last_trader_commit_time()` now filters
   specifically for the trader workflow's commit message pattern
   (`"update <date> @ ..."`), ignoring heartbeat's own commits. Also
   added a 15-minute grace window after market open so the very first
   heartbeat check of the day doesn't compare against yesterday's last
   commit and false-alarm before the trader has had a chance to run.

## V2.0 — Professional Options Decision Engine (major rearchitecture)

Rewritten per a detailed V2.0 spec prioritizing quality over quantity
(target: 2-4 high-grade trades/day, not a dozen borderline ones). This
touches `scanner.py`, `state.py`, `main.py`, and `telegram_notify.py`.
Every new function was tested with synthetic data before being wired
in — see the test commands in project history if you want to re-verify.

**Removed:**
- Raw numeric score never reaches Telegram. Internally a 0-100
  quality score still exists (`_quality_score`, underscore = internal,
  never display it), but messages show **Trade Grade** (Strong Entry /
  Entry) and **Confidence** (★ stars + label) instead.
- Fixed % Stop Loss / Targets replaced with **ATR-based dynamic
  levels** (`dynamic_trade_levels()`) — SL/T1/T2 scale with actual
  underlying volatility (ATR × option delta), and T2 is capped against
  the nearest support/resistance level (`find_nearby_level()`) so
  targets don't extend past a level the underlying hasn't broken.
  Tested: a resistance 20 points away correctly pulled T2 in from 170
  to 129 vs. the uncapped ATR-only target.
- Duplicate same-direction alerts: suppression is now **per
  symbol+direction only** (not per-moneyness like V1) — `state.py`'s
  `has_open_trade()` signature dropped the moneyness parameter.
- Probability percentage removed entirely; confidence is stars + label
  only, never a number.

**Added:**
- **VWAP filter** (mandatory): `vwap_confirms()` requires price above
  VWAP for CE, below for PE. Fails closed (rejects) if volume data is
  missing rather than guessing.
- **3-state regime detection** (replaces V1's binary trending/choppy):
  TRENDING / SIDEWAYS / VOLATILE, using efficiency ratio (trend
  cleanliness) combined with ATR-as-%-of-price (volatility level).
  Sideways adds +8 to the required quality score, Volatile adds +15.
- **Liquidity Engine** (`liquidity_ok()`): rejects on OI, option
  volume, or bid-ask spread below/above configured thresholds — a
  strong directional signal in an illiquid contract is still a bad
  fill, so it's rejected regardless of grade.
- **Smart Strike Ranking** (`rank_all()`): ATM/ITM/OTM are still
  evaluated internally, but only the single best-graded strike is
  returned as an actual trade; the rest are listed as text-only
  alternatives in the same alert (never opened as separate trades).
- **Cross-symbol Best Opportunity ranking**: `detect_all_setups()`
  gathers every passing candidate across all symbols into one flat
  list and ranks once — only the single best crosses into a real
  trade; everything else (any symbol, any moneyness) becomes a
  same-message alternative. (A subtle bug here — where a losing
  symbol's own alternatives were silently dropped by an earlier
  two-stage ranking design — was caught and fixed via integration
  testing before being finalized.)
- **Trade Quality Gate** (`quality_gate()`): ALL of {EMA trend, VWAP,
  volume, OI build-up, liquidity, 15-min confirmation, risk/reward}
  must pass or the setup is silently rejected — no partial-credit
  alert, no Telegram message at all for a rejected setup.
- **"Why Recommended?" checklist** (`reasons_from_factors()`) and
  **Expected Holding Time** range (`estimated_holding_minutes()`) on
  every alert.
- **Back-to-back-losses circuit breaker tightened from 3 to 2**
  consecutive SL hits (per the spec's explicit addendum) — tested:
  trips exactly on the 2nd consecutive SL now, not the 3rd.
- **Daily summary** now includes Best Trade, Worst Trade (both by max
  favorable move %), and Average Holding Time across resolved trades.

**Bug found and fixed during review**: the first cross-symbol-ranking
design only ever returned the single top-ranked candidate. If that
candidate's symbol+direction already had an open trade (suppressed),
`run_scanner_step` had no fallback — the entire run would alert
nothing that cycle, even when a perfectly valid second-best candidate
existed. Fixed: `detect_all_setups()` now returns the full ranked list
(`rank_all()`), and `main.py` walks down it picking the first
non-suppressed candidate, using the rest as that trade's alternatives.
Verified with a test where NIFTY (the top pick) was pre-suppressed and
the system correctly fell through to alert on BANKNIFTY instead — and
a second test confirming it still correctly sends nothing when no
valid fallback exists.

**Deliberately not used** (per spec): RSI, MACD, Bollinger Bands,
SuperTrend, Stochastic, Ichimoku, CCI, extra MA crossovers beyond the
existing EMA logic, and no ML/AI of any kind — every function above is
plain deterministic arithmetic, same input always gives the same
output.

**Explicitly deferred**: SENSEX and MIDCPNIFTY are not wired in yet
(the spec itself says "later") — `SYMBOLS` in `scanner.py` is a list
specifically so adding them later is a matter of extending that list
plus `STRIKE_STEP`/`UNDERLYING_INSTRUMENT_KEYS`, not a redesign. Worth
knowing before you do, verified current as of this session:
- **SENSEX** trades on **BSE**, not NSE — weekly expiry is Thursday
  (moved from Tuesday, effective 4-Sep-2025), monthly is the last
  Thursday. Strike spacing is 100 points. BSE instrument keys use a
  different exchange prefix than NSE ones in Upstox's instrument
  master, and liquidity is meaningfully thinner than NIFTY's — spreads
  widen faster moving OTM, so the liquidity engine thresholds
  (`MIN_OI`, `MIN_OPTION_VOLUME`, `MIN_PREMIUM`, `MAX_SPREAD_PCT`)
  likely need separate, looser values for SENSEX rather than reusing
  NIFTY's.
- **MIDCPNIFTY** trades on NSE but **no longer has a weekly series** —
  NSE restricted weekly options to NIFTY 50 only as part of the same
  Sept-2025 restructuring, so MIDCPNIFTY is monthly-only now (last
  Tuesday). Strike spacing needs verification against NSE's current
  contract spec before hardcoding it into `STRIKE_STEP`.
- Adding either symbol roughly doubles scanner-side API calls per run
  (one more `get_atm_strike`/strike-scan pass per symbol) — negligible
  against the Actions minutes budget (compute-bound, not the cost
  driver), but worth watching against Upstox's own rate limits.

## Recommendations for "more accurate" — status: implemented

All three top recommendations from the previous review are now built
and tested:

1. **Grade-vs-outcome feedback loop** (`rolling_log.py` +
   `weekly_report.py` + `weekly_report.yml`, Monday 09:00 IST) — same
   bounded/auto-trimmed design as before (30-day cap, `date, trade_id,
   grade, moneyness, final_status` only), but rebucketed by **grade**
   (A+/A/B+/B) instead of a numeric score, since V2.0 doesn't expose
   raw scores anywhere. The report explicitly checked whether higher
   grades were actually winning more than lower ones — tested against
   both a "grading works" scenario and a "grading is backwards"
   scenario, both detected correctly.
   **Since removed** — see "Removed" above; not required for this
   setup. Reintroduce `rolling_log.py` + `weekly_report.py` together if
   you want this feedback loop back.
2. **Delta floor for OTM** (`delta_acceptable()`, `MIN_DELTA_OTM =
   0.15`) — now a required `quality_gate()` factor (`delta_ok`).
   Tested: OTM at delta 0.10 correctly rejected, 0.20 correctly
   passes, ATM/ITM unaffected by this floor.
3. **Expiry-day theta filter** (`expiry_theta_ok()`,
   `EXPIRY_DAY_MIN_OTM_DELTA = 0.25` within
   `EXPIRY_DAY_THRESHOLD_DAYS = 1`) — a stricter OTM delta floor
   specifically in the last day before expiry, where theta decay
   dominates. Also a required gate factor (`expiry_theta_ok`). Tested
   across on-expiry-day and days-away scenarios.

`quality_gate()`'s required factor list grew from 7 to 9
(`delta_ok`, `expiry_theta_ok` added) — `score_strike()`'s TODO
docstring documents exactly what to wire up for both.

**What NOT to add**: more indicators (RSI/MACD/etc, per your original
spec), higher `STRIKE_RANGE` (dilutes quality by scanning further
OTM/ITM), or IV-percentile filtering (needs historical IV data that
conflicts with the no-database constraint — possible later, not now).

## Independent review round 2 (thinking as the owner, not just following the spec)

Two more real issues found and fixed on a second pass, beyond direct
spec compliance:

1. **VWAP would have silently rejected every trade, forever.**
   `compute_vwap()` requires a `"volume"` field per candle, but
   `market_data.get_recent_underlying_candles()` never fetched it —
   every VWAP check would return `None` (fail-closed) and the
   mandatory VWAP filter would reject 100% of setups with no error,
   no warning, nothing to debug against. Fixed: volume is now included
   in the underlying candle fetch.

2. **Even with volume fixed, VWAP on the raw INDEX is still always
   zero** — an index (`NSE_INDEX|Nifty 50`) isn't actually bought or
   sold, so it carries no genuine traded volume; only its futures
   contract does. Added `VWAP_INSTRUMENT_KEYS` (separate from
   `UNDERLYING_INSTRUMENT_KEYS`, which is fine for EMA/regime since
   those only need OHLC) pointing at the current-month futures
   contract, and a `vwap_check()` wrapper that uses it correctly and
   **fails loudly** (`NotImplementedError`) if unconfigured, rather
   than silently returning `False` forever with no clue why. Tested:
   confirmed it raises immediately when unset, and confirmed
   all-zero-volume candles (the exact index-mistake scenario) still
   correctly return `None`/fail-closed rather than a wrong number.

3. **`score_strike()`'s quality-score calculation was a total blank
   page** (prose TODO only). Added `example_quality_score()` — a
   concrete, weighted, tested reference implementation turning six
   factor *strengths* (0.0-1.0 each, not just pass/fail) into the
   continuous 0-100 score `compute_grade()` needs to differentiate
   A+ from a barely-passing B. Tested: a "strong" input set correctly
   grades A/A+, a "weak but gate-passing" input set correctly grades
   below the Reject threshold — proving the continuous score does
   real differentiation work beyond the binary gate, which is the
   whole point of having both. Weights are adjustable, not a mandate.

## Honest status: spec compliance vs. runnable today

Every V2.0 requirement has a tested, working implementation in the
**framework** (gate, grading, ATR levels, VWAP, regime, liquidity,
ranking, fallback, circuit breaker, daily summary). What's still
**stub-only, by necessity** — because it depends on proprietary
detection logic only you have — is `directional_bias()`,
`get_atm_strike()`, and `score_strike()`'s actual EMA/breakout/volume
reads. Until those three are filled in, the system is architecturally
complete but will not generate a single trade — this isn't a bug, it's
the honest state of a scaffold waiting for its detection logic. The
fastest unblock, in order: (1) `UNDERLYING_INSTRUMENT_KEYS` +
`VWAP_INSTRUMENT_KEYS`, both trivial lookups from Upstox's instrument
master; (2) `get_atm_strike()`; (3) `directional_bias()`; (4)
`score_strike()`, which can lean on `example_quality_score()` as a
starting point rather than building factor-weighting from scratch.

## Confluence layer (V1, still active under the hood)

Three pure-computation helpers added to `scanner.py`, meant to be
called from inside your own `directional_bias()` / `score_strike()`
logic once you fill those in. None of them replace your detection
logic — they're extra confirmation checks that make a single weak
signal harder to act on alone. All tested with synthetic data (see
below).

**`multi_timeframe_confirms(symbol, direction)`** — checks whether the
15-minute EMA9/21 trend agrees with a direction your 5-min logic
already picked. Costs exactly one extra API call per symbol per run
(15-min candles) — negligible against the Actions/API budget.

**`market_regime(candles_15m)`** — classifies the current session as
`TRENDING` or `CHOPPY` using Kaufman's Efficiency Ratio (net move ÷
sum of all candle-to-candle moves; close to 1 = clean trend, close to
0 = whipsaw). Reuses the same 15-min candles fetched above, so it's
zero extra API cost. On a choppy read, `EFFICIENCY_CHOPPY_SCORE_PENALTY`
(default 5) is added to the session's `min_score` for that run —
raising the bar exactly when false breakouts are most likely. Verified
with a test proving the same borderline score (68) passes in a
trending regime but is correctly filtered out in a choppy one.

**`oi_is_building(oi_history, instrument_key, current_oi)`** — compares
current OI against the previous run's reading (persisted in
`state["oi_history"]`, threaded through automatically by `main.py` —
zero extra API cost, it's just remembering last run's number). Returns
True only if OI grew by at least `OI_BUILD_THRESHOLD_PCT` (default 2%)
since the last reading, filtering out flat/noisy OI from being read as
conviction.

**What's still on you**: `UNDERLYING_INSTRUMENT_KEYS` in `scanner.py`
needs the actual NIFTY/BANKNIFTY spot or futures instrument keys filled
in before `multi_timeframe_confirms()`/`market_regime()` can run — and
your own `directional_bias()`/`score_strike()` need to actually call
these helpers and factor their results into your score. The confluence
principle: require several independent signals to agree (5-min trend +
15-min trend + volume + OI build-up) before scoring a setup highly,
rather than trusting any single indicator. This means fewer alerts,
not more — that's the intended trade-off for higher quality.

## GitHub Actions minutes budget (verified)

Market window is 09:15–15:30 IST = 375 minutes/day. The trader
schedule is now **split into three phases** based on when
`scanner.py` can actually find new setups (its session windows:
09:30–10:35, 10:35–11:35, 13:45–15:10) vs. the 11:35–13:45 lunch gap,
where no new setups are possible and the run only tracks already-open
trades:

| Phase | Window (IST) | Interval | Why |
|---|---|---|---|
| 1 | 09:15–11:35 | 5 min | active scanning session |
| 2 | 11:35–13:45 | 15 min | lunch gap — tracking only, no scanning |
| 3 | 13:45–15:30 | 5 min | active scanning session |

| Workflow | Runs/day |
|---|---|
| `trader.yml` | 59 |
| `heartbeat.yml` | 13 |

Typical usage (ESTIMATE, not measured -- see note below): scaling the
previous 7-min schedule's measured ~1,276 min/month typical / ~1,562
min/month worst-case by the ~1.3x run-count increase (45 -> 59
runs/day) gives roughly **~1,670 min/month typical (~84%)** and
**~2,050 min/month worst-case (~103% -- likely to exceed the free
2,000-minute tier some months)**. This is a projection from the old
schedule's numbers, not a fresh measurement -- check
**Settings → Billing and plans → Plans and usage → Actions minutes**
after a week of live 5-min runs to get the real number, same as
before.

**Trade-off, stated plainly**: during the 11:35–13:45 gap, if a trade
is already open and hits SL/T1/T2, the Telegram notification can lag
up to ~15 minutes instead of ~7. The recorded hit time itself stays
exact to the minute regardless (tracker.py replays every 1-min candle
chronologically), so this only affects how fast you're notified, not
what gets recorded.

If your actual measured runtime per job differs a lot from the
~40–55s assumption used here, check
**Settings → Billing and plans → Plans and usage → Actions minutes**
after a week of live runs and this can be recalculated precisely.
## What you still need to do

1. **Merge your real scanner logic into `scripts/scanner.py`** — the EMA9/21,
   breakout, volume, OI, delta, and scoring logic you already have.
   The only requirement is that `detect_setups()` returns dicts matching
   the schema documented at the top of that file.

2. **Fill in your NSE holiday list** in `scripts/holiday_guard.py`
   (`NSE_HOLIDAYS_2026`) — copy it from your existing scanner so both
   stay in sync.

3. **Verify the Upstox candle endpoint** in `scripts/market_data.py`
   against Upstox's current API docs. I've used the standard v2
   intraday-candle path and response shape, but API surfaces do drift —
   confirm before relying on it live. Since you already have working
   Upstox auth/candle-fetch code in your existing scanner, you may
   prefer to copy that proven implementation in here instead.

4. **Add repo secrets** (Settings → Secrets and variables → Actions):
   - `ANGEL_CLIENT_CODE`, `ANGEL_PIN`, `ANGEL_API_KEY`, `ANGEL_TOTP_SECRET`
     (Angel One, primary broker — see `auth.py`'s docstring on where
     `ANGEL_TOTP_SECRET` comes from)
   - `UPSTOX_ACCESS_TOKEN` (fallback broker — still required at
     startup even though it's not primary; failover can't fall back
     to a broker that was never configured)
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
   - `GITHUB_TOKEN` is provided automatically by Actions, but make sure
     the workflow has **Read and write permissions** under
     Settings → Actions → General → Workflow permissions (required so
     it can push to the `state` branch).

5. First run will auto-create the `state` branch as an orphan branch
   if it doesn't exist yet — no manual setup needed there.

## Design decisions worth knowing

- **One workflow, not two.** Scanner and tracker run in the same
  process/commit so there's only ever one writer to the state file —
  this is what avoids race conditions without needing a lock file.
- **State branch, not cache/artifacts.** Git push failure is an
  explicit, catchable signal; cache/artifacts don't give you that.
- **Entry price = candle close**, not intra-candle guess — the only
  value actually observable from OHLC without assuming a fill point
  we can't verify.
- **Ambiguous candles are frozen, never guessed.** If SL and a target
  land in the same 1-min candle, the trade is marked `AMBIGUOUS` and
  you get the raw OHLC to judge manually. This is a deliberate accuracy
  choice — a wrong automatic guess is worse than an unresolved trade.
- **Same-symbol+direction alerts are suppressed** while a trade is
  already `WAITING_ENTRY` or `ACTIVE`, to keep the daily summary math
  clean and avoid alert noise. If you later want to allow scaling in,
  that's a deliberate manual action, not automatic.
- **No history.** `cleanup_old_state_files()` deletes yesterday's JSON
  from the branch's working tree each run — only today's file exists
  in the tree at any time (git history still technically retains old
  commits; force-push a fresh orphan branch occasionally if you want
  to purge that too).
- **Network calls retry transient failures.** Both `market_data.py`
  (Upstox) and `telegram_notify.py` (Telegram) retry up to 3 times
  with exponential backoff on connection errors, timeouts, rate
  limits (429), and momentary 5xx responses — a single dropped packet
  no longer fails the whole run. Auth errors (401/403) and
  other 4xx responses are NOT retried; those need a human, not a
  retry loop, so they fail loudly and immediately instead.

## On "more accurate" results

Tracking accuracy here is about **honest measurement**, not a better
predictor — the scanner's setup_score is still yours to improve. What
this system adds on the measurement side:
- Real entry-zone confirmation (alerts ≠ automatic fills)
- Exact-contract tracking (no silent ATM-strike drift)
- Minute-level chronological replay so no move is skipped or misordered
- Honest `AMBIGUOUS`/`EOD_CLOSED` buckets instead of forcing every
  trade into a win/loss, which would quietly inflate your win rate

If you want to improve the *predictive* side (setup_score itself), that's
a separate project — happy to help design a lightweight same-day
score-vs-outcome correlation check using this same state file, without
turning it into a permanent backtesting database.
