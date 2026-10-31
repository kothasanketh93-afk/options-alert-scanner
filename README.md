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
| `scripts/broker.py` | Abstract broker interface (`get_candles`, `get_ltp`, `get_option_chain`, `search_instrument`, `get_current_expiry`). Both `angelone.py` and `upstox.py` implement this — scanner.py/tracker.py never know which one actually served a call. Also documents two non-abstract conventions both concrete brokers should implement if they support futures/stocks: `resolve_current_month_future()` and `resolve_equity_instrument_key()`. |
| `scripts/angelone.py` | Concrete Angel One SmartAPI implementation of `broker.py` — the **primary** broker. Automated TOTP login, no manual step ever. Now handles both index (`OPTIDX`/`FUTIDX`) and stock (`OPTSTK`/`FUTSTK`) instrument-type codes for `config.STOCK_SYMBOLS` — see `_option_instrument_type()`/`_future_instrument_type()`. |
| `scripts/upstox.py` | Concrete Upstox implementation of `broker.py` — now the **fallback** broker, used only when Angel One is unhealthy. Resolves stock underlyings dynamically via `resolve_equity_instrument_key()` (instrument master lookup) rather than a hardcoded key. |
| `scripts/broker_failover.py` | Wraps both brokers behind the same `Broker` interface. As of 2026-07-23, Angel One is primary again and Upstox is fallback (swapped back from the 2026-07-21 Upstox-primary change — see "Bugs found and fixed" #10 and its follow-up note) — switches to the fallback after consecutive primary failures (auth, timeout, empty/stale data, rate limits, etc.), logs every switch, and automatically tries the primary first again on the next run — no persisted cross-run state needed. Passes through `resolve_current_month_future()` and `resolve_equity_instrument_key()` (both `count_failures=False`, same as other data-specific lookups). See its module docstring for the full design and reasoning. |
| `scripts/cache.py` | Tiny in-process TTL cache (instrument master, option chain, underlying candles) scoped to one GitHub Actions run. |
| `scripts/logging_setup.py` | Structured logging to named channels, written to stdout (captured by the Actions run log) rather than to disk files that would vanish with the ephemeral runner — see its docstring for the reasoning and how to add persisted log artifacts if you want them later. |
| `scripts/state.py` | Loads/saves the daily state file via the `state` git branch; generates Trade IDs; same-symbol+direction suppression check; **circuit breaker + concurrent trade cap**; **auto-recovers from a corrupted state file** instead of crashing. |
| `scripts/tracker.py` | Chronological 1-min candle processing: entry activation, T1/T2/SL, ambiguous-candle detection, EOD handling |
| `scripts/market_data.py` | Thin compatibility wrapper over `broker.py`/`broker_failover.py` — preserves the exact function signatures scanner.py/tracker.py already call, so the broker abstraction (and later, failover) required zero changes to either. |
| `scripts/telegram_notify.py` | All message templates (new alert, entry, T1, T2, SL, not-entered, EOD, ambiguous, daily summary) — message formats untouched by the architecture work; only the send-plumbing changed. |
| `scripts/scanner.py` | Detection flow: universe → opportunity shortlist (Phase 1) → dynamic strike selection (Phase 2) → Trade Score Engine (Phase 3, via `trade_score.py`). Scans ATM + ITM + OTM per direction, one best strike per symbol+direction alerted. |
| `scripts/trade_score.py` | **Phase 3**: continuous-strength Trade Score Engine — Trade Score /100, Probability %, Confidence /10, Risk /10, Reward /10, Grade (A+/A/B+/B), Expected R:R. Replaces `grading.py`'s old binary-strengths scoring in the active path (`grading.py` itself is left in place, unused, as a fallback/reference). |
| `scripts/universe.py` | **Phase 1**: the ~100-stock master universe (F&O-eligible, liquid, sector-tagged, configurable). |
| `scripts/opportunity_score.py` | **Phase 1**: pre-market Opportunity Score (0-100) — rvol, gap, ATR, momentum, OI change, sector strength, liquidity, VWAP distance, prev-day range. |
| `scripts/build_opportunity_shortlist.py` | **Phase 1**: daily pipeline that scores the full universe and shortlists the top `TOP_N_STOCKS` for detailed scanning. |
| `scripts/instrument_master.py` | **Phase 2**: dynamic strike engine — downloads/caches the instrument master once/day, validates F&O eligibility/expiry/lot size/strike availability with skip-reasons, resolves ATM/ITM/OTM from real listed strikes (no hardcoded steps). |
| `scripts/build_instrument_master_cache.py` | **Phase 2**: once-daily instrument master download/cache-build script. |
| `scripts/heartbeat_check.py` | Independent staleness check — verifies Angel One reachability, Upstox reachability+auth, Telegram reachability, and state-branch writability every run (not just staleness), and actually checks git's exit code instead of assuming success |
| `scripts/premarket_check.py` | Runs once daily, ≥15 min before market open — same diagnostics as heartbeat_check.py, so a misconfiguration or outage is caught before the trading day starts rather than 45+ minutes into it |
| `scripts/holiday_guard.py` | Weekend + NSE holiday check, skips runs on non-trading days |
| `scripts/main.py` | Orchestrator run every 5 min (15 min in the lunch-gap phase): validate config → scanner → tracker → notify → save |
| `.github/workflows/trader.yml` | Main job: scanner + tracker, 5-min IST schedule (15-min during the lunch gap), concurrency-protected |
| `.github/workflows/heartbeat.yml` | Runs every 30 min independently, alerts if state branch goes stale |
| `.github/workflows/premarket.yml` | Runs once daily at 09:00 IST, own workflow, own concurrency group |
| `.github/workflows/verify-config.yml` | `workflow_dispatch` only, triggered quarterly by an external cron-job.org job (see "Cron scheduling" below); posts a Telegram summary only on failure |

**BTST (overnight) module — separate from everything above, see its own section below:**

| File | Purpose |
|---|---|
| `scripts/btst_config.py` | Every BTST-specific tunable — symbol universe, entry window, wider SL/target %, min days-to-expiry, concurrent-position cap. Not imported by any intraday file. |
| `scripts/btst_levels.py` | Overnight SL/target sizing — plain percentage-of-premium math, independent of `levels.py`'s intraday ATR engine. |
| `scripts/btst_calendar.py` | Next/previous NSE trading-day lookup, reusing `holiday_guard.py`'s holiday data. |
| `scripts/btst_earnings_guard.py` | Manual earnings/event blackout list for BTST stocks — **defaults to blocking a symbol, not allowing it**, until a human has reviewed and entered its calendar. Currently empty — see "What you still need to do" below. |
| `scripts/btst_state.py` | Overnight position persistence, built on `daily_cache.py`'s existing git read/write (own subdir, never touches `state.py`'s intraday `state/<date>.json` files). |
| `scripts/btst_scanner.py` | Evening entry scan — reuses `scanner.score_strike()` unmodified for signal detection, swaps in overnight levels/filters on top. |
| `scripts/btst_tracker.py` | Exit-day candle processing: gap-check at the open, then T1/T2/SL/force-exit. |
| `scripts/btst_telegram.py` | BTST message templates, reusing `telegram_notify.send()`. |
| `scripts/btst_main.py` | Orchestrator — `python btst_main.py --entry` (run near market close) and `--exit` (run through the next trading day). No `.yml` workflow wired up yet — see below. |

## Cron scheduling

None of the workflows above use GitHub Actions' native `schedule:`
trigger — every `.yml` file is `workflow_dispatch`-only, and actual
timing is driven by an external service, **cron-job.org**, whose jobs
call the GitHub API's workflow-dispatch endpoint directly on a
schedule cron-job.org controls (visible on the cron-job.org
dashboard as `Trader-1` through `Trader-8`, `Heartbeat-1`/`Heartbeat-2`,
`Premarket-1`, and now `verify-config`'s own job). Each call is an
authenticated `POST` like:

```
POST https://api.github.com/repos/<owner>/<repo>/actions/workflows/<workflow-file>/dispatches
Headers: Authorization: token <GITHUB_TOKEN>, Accept: application/vnd.github+json
Body: {"ref": "main"}
```

Most likely reason for this over native `schedule:`: GitHub's own cron
scheduler can queue/drift by several minutes under platform load,
which is tolerable for a once-a-day or 30-min job but not for
`trader.yml`'s 5-min scan cadence during market hours — splitting the
trading day across 8 separate cron-job.org jobs (`Trader-1..8`,
apparently one per time-slot) rather than one recurring job is
presumably the same precision consideration, though the exact
per-job time-slot breakdown lives in the cron-job.org dashboard, not
in this repo.

**Implication for anyone editing a workflow's timing**: changing a
`.yml` file's trigger comment or adding a `schedule:` block here does
**nothing** by itself — the actual schedule change has to be made on
the cron-job.org dashboard (Cronjobs → the relevant job → Edit). Two
places to keep in sync, not one.


## Phase 1–3 status (100-stock expansion + Trade Score Engine)

**Phase 1 — Opportunity Score Engine: done.** `universe.py` holds the
~100-stock master universe; `opportunity_score.py` computes the
pre-market 0-100 score; `build_opportunity_shortlist.py` picks the
top `config.TOP_N_STOCKS` (35) for detailed scanning, cutting API
calls/runtime by ignoring the rest.

**Phase 2 — Dynamic Strike Selection: done.** `instrument_master.py`
downloads/caches the instrument master once/day and resolves
ATM/ITM/OTM from real listed strikes — no hardcoded strike tables or
divisors anywhere. Skips log a reason for every skip.

**Phase 3 — Professional Trade Score Engine: done, with two explicit
decisions worth knowing about:**
1. **Numbers are shown in Telegram again.** An earlier rewrite
   (documented below as "V2.0") deliberately removed all raw
   numbers from alerts in favor of Grade + stars + emoji only. Phase
   3 reverses that — Trade Score, Probability %, Confidence, Risk,
   Reward, and Expected R:R are now all shown (see
   `telegram_notify.py`'s `fmt_new_alert()`), with the old
   grade/stars/emoji kept alongside them, not replaced.
2. **RSI and PCR are NOT used**, even though the phase-3 spec assumed
   both were already being calculated. Neither was (RSI is
   deliberately excluded — see scanner.py's docstring; PCR was never
   implemented) — rather than bolt on two new indicators, the
   10-factor formula (`trade_score.py`) is built entirely from what's
   already computed elsewhere: EMA, VWAP, ATR/trend-efficiency, OI
   build, liquidity, option volume, 15-min MTF confirmation, and R:R.
   "Market Strength" is filled by NIFTY's own trend-efficiency reading
   as a broad-tape-conviction proxy, since nothing more specific
   (sector breadth, advance/decline) is wired in yet.

None of the weights, ceilings, or the probability/risk/reward
calibration formulas in `trade_score.py` are backtested — they're
reasoned starting points, same caveat as `opportunity_score.py`'s
weights. Tune both from live `SCORE`/`GATE` log lines once real
outcomes accumulate, same feedback loop already described for grading
further down this file.



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

## Mid/smallcap stock options (added 2026-07-17, superseded by Phase 1)

`config.SYMBOLS` includes stock options in addition to NIFTY/
BANKNIFTY/MIDCPNIFTY. `scanner.py`/`option_chain.py` scan them the
same way as the indices — no per-symbol code branching needed on
that side.

**This started as a fixed list, now it's dynamic** (full detail in
"Phase 1–3 status" above): `config.STOCK_SYMBOLS` is refreshed every
day via `config.refresh_symbols_for_today()` from Phase 1's computed
opportunity shortlist — the top `config.TOP_N_STOCKS` (35) scored out
of the ~100-stock `universe.MASTER_UNIVERSE`. The original fixed list
now lives on as `config.FALLBACK_STOCK_WATCHLIST` — 15 symbols
(`TATASTEEL`, `HINDALCO`, `VEDL`, `JINDALSTEL`, `IDEA`, `SAIL`,
`NATIONALUM`, `NMDC`, `BHEL`, `FEDERALBNK`, `BANKBARODA`, `PFC`,
`RECLTD`, `ASHOKLEY`, `MOTHERSON`) — used only as a safety net on a
day the shortlist cache is missing (premarket pipeline hasn't run, or
failed), so a bad day degrades to the old fixed-list behavior rather
than scanning nothing.

This fallback list went through two revisions after live failures
(see "Bugs found and fixed" below): `PNB` and `CANBK` were both
dropped after repeatedly failing futures-contract resolution live,
before it grew to its current 15. These were chosen for historically
high, consistent option/futures volume (metals/PSU/telecom/banking
names) specifically to reduce the odds of hitting the same
thin-liquidity-driven data gaps again — this is a risk reduction, not
a guarantee. `.github/workflows/verify-config.yml` runs
`verify_stock_symbols.py` quarterly to catch drift automatically
across BOTH the fallback list and the live `universe.MASTER_UNIVERSE`
pool, and you can also run it manually anytime. **`IDEA` is a special
case**: its share price is very low (~₹7-10), close enough to
`STOCK_MIN_PREMIUM` that it may get rejected by the liquidity gate
more often than the others — watch its `GATE`/`SCORE` log lines
specifically for the first few days.

What's different for stocks vs. indices, and where it's handled:

- **Instrument key resolution** — indices use a static hand-typed
  key (`option_chain.UNDERLYING_INSTRUMENT_KEYS`); stocks have no
  equivalent stable key (an equity's key is ISIN-based), so it's
  resolved fresh each run via `Broker.resolve_equity_instrument_key()`
  — implemented in both `upstox.py` and `angelone.py`, routed through
  `option_chain.resolve_underlying_key()`.
- **Strike interval** — `config.STOCK_STRIKE_STEP`, merged into the
  existing `STRIKE_STEP` dict.
- **Liquidity thresholds** — `config.STOCK_MIN_OI` /
  `STOCK_MIN_OPTION_VOLUME` / `STOCK_MIN_PREMIUM`, applied instead of
  the index-tuned globals via `filters.liquidity_ok(..., symbol=...)`.
- **Angel One instrument-type codes** — Angel One's scrip master uses
  `OPTSTK`/`FUTSTK` for stocks vs. `OPTIDX`/`FUTIDX` for indices;
  `angelone.py` now branches on this (`_option_instrument_type()` /
  `_future_instrument_type()`) everywhere it used to hardcode the
  index-only code.

**Debugging tools added alongside this feature:**

- **`verify_stock_symbols.py`** (repo root) — downloads the same two
  public instrument masters `upstox.py`/`angelone.py` use at runtime
  (no broker login needed) and checks: the full `FALLBACK_STOCK_WATCHLIST`
  (equity resolution, F&O eligibility, and strike interval vs.
  `STOCK_STRIKE_STEP`), plus every remaining symbol in
  `universe.MASTER_UNIVERSE` (equity/F&O eligibility only, since most
  don't have a hand-verified strike step configured) — this second set
  matters because on most days the live `STOCK_SYMBOLS` list is Phase 1's
  computed shortlist out of the ~100-stock universe, not the fixed
  fallback 15 (see `config.refresh_symbols_for_today()`). Both symbol
  sets and the configured strike steps are imported directly from
  `config.py`/`universe.py`, not hand-copied, so this script can't drift
  out of sync with them. Run manually with `python verify_stock_symbols.py`
  after `pip install requests`, or via `.github/workflows/verify-config.yml`
  (below). Can't check Angel One's strike-divisor heuristic (below) since
  that needs a live-quote sanity check, not just the raw master.
- **`.github/workflows/verify-config.yml`** — runs `verify_stock_symbols.py`
  on `workflow_dispatch` only, triggered quarterly by an external
  **cron-job.org** job (same scheduling mechanism as `Heartbeat-1/2`,
  `Premarket-1`, and `Trader-1..8` on the cron-job.org dashboard — this
  project does NOT use GitHub Actions' native `schedule:` trigger
  anywhere, see "Cron scheduling" below) calling the GitHub API's
  workflow-dispatch endpoint directly, plus manual dispatch from the
  Actions tab anytime. On any failure it posts a summary to Telegram via
  `telegram_notify.py` (`TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` secrets,
  same as the main scanner alerts) — a clean pass stays silent, so this
  doesn't add noise to the channel.
- **`scripts/verify_angelone_login.py`** + **`.github/workflows/
  verify_angelone.yml`** — tests ONLY the Angel One login step
  (clientcode+PIN+TOTP), via a manual `workflow_dispatch` run, without
  waiting for a live scan window. Useful for isolating "is it the
  login, or something after login" when Angel One is failing.
- **`scanner.py` per-strike logging** — `score_strike()`/
  `detect_setups()` now log `BIAS`/`SKIP`/`GATE`/`SCORE` lines for
  every symbol/strike evaluated, not just ones that end up alerting.
  Before this, a fully-quiet day (nothing alerted) was
  indistinguishable in the log from the scan not running at all.
  `BIAS` = no directional bias this run; `SKIP` = strike/leg data
  unavailable; `GATE` = hard risk gate (liquidity/delta/theta)
  pass/fail with every factor; `SCORE` = weighted score/grade for
  anything that cleared the hard gate.

**Still UNVERIFIED beyond what `verify_stock_symbols.py` checks**
(same standard this README already holds MIDCPNIFTY's strike step
and the holiday list to): Angel One's strike-divisor auto-detect
heuristic (`_detect_strike_divisor()`), which was magnitude-calibrated
for index-scale prices and may misfire on a lower-priced stock — pull
one known strike's premium from a live quote and sanity-check it
against what the scanner computed. `config_validation.py` prints a
startup reminder listing the configured stock symbols so this isn't
easy to forget. `STOCK_MIN_OI`/`STOCK_MIN_OPTION_VOLUME`/
`STOCK_MIN_PREMIUM` are a starting guess, not backtested — tune from
the `SCORE` log lines the same way `ENTRY_SCORE`'s comment in
`config.py` describes doing for the weighted-score cutoff (also
lowered, from 65 to 60, on 2026-07-17 while testing for near-miss
alerts — see that comment for the caveat on what this does and
doesn't fix).

**Runtime / Actions-minutes impact**: at the original 2026-07-17
change, `SYMBOLS` went from 3 entries to 13 (3 indices + the fixed
10-stock list back then). **Since Phase 1, this is higher and
variable, not fixed**: `SYMBOLS` is now 3 indices +
`config.TOP_N_STOCKS` (35) on a normal day the opportunity shortlist
computed successfully, or 3 + 15 on a fallback day
(`FALLBACK_STOCK_WATCHLIST`) — i.e. up to 38 entries, not 13. Each
extra symbol is its own set of broker calls per run (candles, option
chain, VWAP futures resolution, ATM strike), so per-run wall-clock
time is well above the ~40–55s figure the "GitHub Actions minutes
budget" section below was measured against, and `trader.yml`'s
`timeout-minutes: 10` has much less headroom than it did at 3
symbols. No `.yml` file needed a code change for the stock symbols
themselves (nothing in `.github/workflows/*.yml` hardcodes a symbol
list or count), but re-check actual measured runtime (Settings →
Billing and plans → Plans and usage → Actions minutes) after a week
of live ~38-symbol runs, the same way that section already
recommends doing after any schedule change. If runtime becomes a
problem, `config.TOP_N_STOCKS` is the lever to pull down before
touching the schedule itself.

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

4. **`upstox.py`/`angelone.py`'s equity resolution matched the wrong
   field for stocks (2026-07-17, live failure on `PNB`).** Both
   brokers' `resolve_equity_instrument_key()` matched an instrument
   master row's `name` field against the plain symbol -- true for
   NIFTY/BANKNIFTY (where `name` already IS the short symbol) but not
   for equity (`EQ`) rows, where Upstox's `name` is the full listed
   company name ("Punjab National Bank", not "PNB"). Fixed: both now
   match on the trading-symbol field first (`trading_symbol` for
   Upstox, a `<SYMBOL>-EQ` convention for Angel One), falling back to
   `name`.

5. **`upstox.py`'s `resolve_current_month_future()` had the same
   field-matching bug for stock FUTURES rows (2026-07-17, live
   failure on `PNB` then `CANBK`).** Same root cause as #4, one level
   up: `name` matched for index futures but not stock futures. Fixed:
   now also matches `trading_symbol` rows starting with the plain
   symbol and ending in `"FUT"` (Upstox's stock-future convention,
   e.g. `"CANBK26JULFUT"`).

6. **One symbol's exception killed the entire run, discarding every
   other symbol's already-computed results (2026-07-17, live
   failure).** `detect_all_setups()` iterated `SYMBOLS` and let any
   exception from one symbol propagate straight out -- confirmed
   live: NIFTY scored 85/100 "Strong Entry" and BANKNIFTY scored
   70/100 "Entry" (both should have alerted), but a later stock in
   the loop raised `InstrumentNotFound` and the whole run aborted
   before `main.py` ever saw either result. Fixed: each symbol's
   `detect_setups()` call is now wrapped in its own `try/except`,
   logged as `SYMBOL_FAILED` and skipped, so one symbol's data
   problem can never again block every other symbol's alerts for the
   whole run.

7. **No visibility into why a fully-quiet run had zero alerts
   (2026-07-17).** Every rejection point in `score_strike()`/
   `detect_setups()` (`quality_gate()` failing, missing directional
   bias, missing leg/delta/candles/ATR) returned silently -- a
   perfectly normal "nothing passed today" looked identical in the
   log to the scan not running at all. Fixed: added `BIAS`/`SKIP`/
   `GATE`/`SCORE` log lines covering every stage (see "Mid/smallcap
   stock options" above for what each means).

8. **`retry_utils.py` discarded the real error body on every 401/403,
   always printing a generic guessed message (2026-07-21).** Every
   OTHER status branch (429, 5xx, other 4xx) already included
   `resp.text[:200]`; the 401/403 branch instead always said "token
   missing/expired/invalid" regardless of what Angel One actually
   returned. This meant days of live debugging (TOTP, IP headers, MAC
   address) happened blind, guessing at a cause instead of reading
   it. Fixed: 401/403 now surfaces the real response body too.

9. **Angel One's historical-candle endpoint was rate-limiting nearly
   every run, and every 401/403 was being treated as a fatal,
   non-retryable auth failure regardless of cause (2026-07-21).** Once
   bug #8's fix let the real body through, the actual message turned
   out to be `"Access denied because of exceeding access rate"` --
   not a credentials problem. The same underlying condition also
   showed up as a `400` with a completely empty body. Fixed in two
   layers: (a) `retry_utils.py` now retries a 401/403/400 that looks
   rate-limited (matching wording, or an empty-body 400) instead of
   raising immediately -- verified with unit tests covering all four
   cases (rate-limited 403, genuine 403, empty-body 400, genuine 400
   with a real message); (b) `angelone.py` added a deliberate
   `ANGELONE_HISTORICAL_MIN_INTERVAL_SECONDS` (1.0s, see `config.py`)
   throttle between historical-candle calls, plus swapped the
   hardcoded `127.0.0.1` client-IP header for a real detected public
   IP (`_detect_public_ip()`) as a secondary hardening measure (this
   turned out NOT to be the actual cause, but is harmless to keep).

10. **Angel One's rate limit persisted even with the throttle above,
    including on the very first call of a fresh process (2026-07-21)**
    -- ruling out this process's own call pacing as the sole cause
    (nothing to throttle against yet on a cold start). Root cause
    undetermined (an account-wide or cross-workflow limit outside
    this codebase's visibility). Rather than continue chasing an
    undocumented limit, `market_data.py` now constructs `FailoverBroker`
    with **Upstox as primary and Angel One as fallback** (previously
    the reverse) -- Upstox served every single request reliably
    throughout the entire debugging session. Angel One is kept as
    fallback, not removed, in case its limit clears or a support
    ticket resolves it. `broker_failover.py`'s `raw_primary()`
    docstring was also corrected -- it's generic, not Angel-One-
    specific, and was previously worded as if it always returned
    Angel One specifically.

    **FOLLOW-UP (2026-07-23): swapped back to Angel One primary, per
    explicit request.** Two things changed since the above: (a)
    Angel One's own SmartAPI forum was found to confirm `getCandleData`
    has a genuine separate 180-requests/MINUTE cap on top of the
    1-request/second one, and a different forum thread separately
    admits Angel One's own rate-limit enforcement is inconsistent even
    for well-under-limit clients; (b) `angelone.py`'s throttle was
    upgraded from fixed-interval-only spacing to a real rolling
    60-second-window limiter enforcing both caps
    (`config.ANGELONE_HISTORICAL_MAX_CALLS_PER_MINUTE`), modeled on
    OpenAlgo's (github.com/marketcalls/openalgo) own fix for the same
    problem. This is a genuine strengthening, not just re-flipping a
    switch -- but it's **not a confirmed fix**, since the original rate
    limit's root cause was never pinned down. Watch the first several
    live runs closely for "Access denied because of exceeding access
    rate" / empty-body 400s specifically on Angel One calls; if it
    recurs, swap `market_data.py`'s `FailoverBroker` constructor args
    back to Upstox-primary (same one-line change either direction).

11. **Telegram messages showed full ISO timestamps with date and
    `+05:30` offset instead of just a clock time (2026-07-21).**
    `tracker.py` stores `actual_entry_time`/`t1_hit_time`/
    `t2_hit_time`/`sl_hit_time` as full `datetime.isoformat()` strings
    (needed for `fmt_eod_closed()`'s holding-duration math), but
    `telegram_notify.py` was displaying those raw strings verbatim.
    Fixed: added a `_time_only()` display helper in `telegram_notify.py`
    that formats down to `HH:MM` for every message; the underlying
    stored values are untouched.

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
ranking, fallback, circuit breaker, daily summary). `directional_bias()`,
`get_atm_strike()`, and `score_strike()` are no longer stubs as of
Phase 2/Phase 3 — `get_atm_strike()` resolves real listed strikes via
`instrument_master.py` (Phase 2, no hardcoded steps/divisors), and
`score_strike()` runs the full Phase 3 Trade Score Engine
(`trade_score.py`) against real EMA/VWAP/OI/volume/momentum reads, not
placeholder logic. What's still worth independently verifying before
trusting this live: the weight/ceiling constants throughout
`trade_score.py` and `opportunity_score.py` are reasoned starting
points, not backtested — see the "Phase 1–3 status" section above for
what to tune once real outcomes accumulate.

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

1. **Nothing to merge here anymore** — `scanner.py`'s detection logic
   (EMA9/21, VWAP, volume, OI, delta, and Phase 3's Trade Score Engine)
   is implemented, not a stub. Worth doing instead: tune
   `trade_score.py`'s/`opportunity_score.py`'s weight and ceiling
   constants against your own live `SCORE`/`GATE` log lines once real
   outcomes accumulate — none of those are backtested yet.

2. **Fill in your NSE holiday list** in `scripts/holiday_guard.py`
   (`NSE_HOLIDAYS_2026`) — copy it from your existing scanner so both
   stay in sync.

2a. **Verify `config.STOCK_SYMBOLS` before trusting stock alerts** —
    `.github/workflows/verify-config.yml` runs `verify_stock_symbols.py`
    on `workflow_dispatch`, confirming every symbol in both
    `config.FALLBACK_STOCK_WATCHLIST` (15 symbols) and
    `universe.MASTER_UNIVERSE` (~100 symbols) is still NSE
    F&O-eligible (revised quarterly), confirming `STOCK_STRIKE_STEP`
    against a downloaded instrument file, and posting a Telegram
    summary if anything fails. **This only runs on a schedule once you
    add a matching quarterly job on the cron-job.org dashboard** (see
    "Cron scheduling" above) — the `.yml` file alone won't trigger
    itself. Still spot-check Angel One's strike-divisor detection
    manually on a real strike for each symbol (see "Mid/smallcap
    stock options" above) — that needs a live quote, not just the
    instrument master. None of this blocks NIFTY/BANKNIFTY/MIDCPNIFTY
    scanning — it only matters once stock
    alerts start firing.

2b. **RE-OPENED 2026-07-23 — Angel One is primary again; watch closely
    for a recurrence.** Original issue (2026-07-21): `verify_angelone_login.py`
    confirmed login itself always succeeded; the actual failure
    (visible only after fixing `retry_utils.py`'s swallowed error body
    — see "Bugs found and fixed" #8) was Angel One's `"Access denied
    because of exceeding access rate"` on `getCandleData`, sometimes
    presenting as a 403 with that text and sometimes as a 400 with an
    empty body. A 1-second per-call throttle
    (`config.ANGELONE_HISTORICAL_MIN_INTERVAL_SECONDS`) didn't fully
    clear it then — the limit persisted even on a fresh process's very
    first call, pointing at an account-wide or cross-workflow limit
    outside this codebase's visibility. That led to swapping Upstox to
    primary (see "Bugs found and fixed" #10). **Swapped back to Angel
    One primary 2026-07-23, per explicit request**, alongside a real
    fix rather than just re-flipping the switch: Angel One's own
    SmartAPI forum confirms `getCandleData` has a genuine separate
    180-requests/minute cap (not just the 1/second one), so the
    throttle was upgraded from fixed-interval spacing to a real
    rolling-window limiter enforcing both
    (`config.ANGELONE_HISTORICAL_MAX_CALLS_PER_MINUTE`, see
    "Bugs found and fixed" #10's follow-up note and
    `angelone.py`'s `_throttle_historical_call()`). Still **not
    confirmed** to fully resolve it — the original root cause was
    never pinned down, and Angel One's own forum separately admits
    their enforcement is inconsistent even for compliant clients.
    Watch the first several live runs' logs closely.

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

## SENSEX support (added 2026-07-23)

Previously deferred (see the old note this replaced in `config.py`):
SENSEX trades on BSE, not NSE, which every broker file assumed until
now. Added by making the exchange segment symbol-dependent instead of
hardcoded, everywhere it was hardcoded — not by adding a parallel
code path, so there's exactly one place each broker decides NFO vs
BFO / NSE vs BSE_FO.

### What changed

- **`config.py`**: `SENSEX` added to `SYMBOLS`, `STRIKE_STEP["SENSEX"]
  = 100` (confirmed: BSE's own SENSEX options page).
- **`option_chain.py` / `upstox.py`**: `"SENSEX": "BSE_INDEX|SENSEX"`
  added to `UNDERLYING_INSTRUMENT_KEYS` (both copies, kept in sync per
  `upstox.py`'s existing comment) — confirmed against multiple
  independent Upstox SDK/code examples using this exact key.
- **`upstox.py`**: a second instrument-master file (`BSE.json.gz`,
  parallel to the existing `NSE.json.gz`) is now fetched and merged —
  **only when `"SENSEX" in SYMBOLS`**, so removing SENSEX from
  `SYMBOLS` restores the exact previous single-download behavior with
  zero other changes. Degrades gracefully (logs an error, does not
  raise) if the BSE fetch specifically fails — a BSE-side hiccup can't
  take down NIFTY/BANKNIFTY's own instrument master, which the same
  method serves. Every other Upstox method (`search_instrument()`,
  `get_current_expiry()`, `resolve_current_month_future()`) already
  matched rows generically by name/expiry/strike/instrument_type with
  no exchange filtering of its own, so merging BSE's rows into the
  same in-memory list was the only change those needed.
- **`angelone.py`**: new `_exchange_segment(name)` helper (`"BFO"` for
  SENSEX, `"NFO"` for everything else — verified: Angel One's own
  SmartAPI forum thread announcing BFO support includes a real
  scrip-master row for SENSEX). Replaces every hardcoded `"NFO"`
  string in `get_option_chain()`, `search_instrument()`,
  `get_current_expiry()`, and `resolve_current_month_future()`. The
  ×100 strike-price scaling (`_detect_strike_divisor()`) and
  `instrumenttype` classification (`OPTIDX`/`FUTIDX`) already worked
  generically and needed no changes — confirmed against a real Angel
  One SENSEX scrip-master row (`strike: 6640000` for an actual 66,400
  strike, `instrumenttype: OPTIDX`, same as NIFTY/BANKNIFTY).

### Verified vs. not verified — read this before trusting it live

Verified this session, from each broker's own public documentation
and forum threads, WITHOUT a live API call (no live broker credentials
available in the environment this was built in):
- The exact exchange segment strings (`BFO`, `BSE_FO`) and instrument
  key format (`BSE_INDEX|SENSEX`)
- A real Angel One scrip-master row shape for a SENSEX option
- SENSEX's ₹100 strike interval
- Upstox's per-exchange instrument-master file naming convention

**NOT verified — a live run against your own account is the only way
to confirm these:**
- That `_fetch_instrument_master()`'s BSE.json.gz merge produces a
  usable master on a live run (only tested against synthetic mock
  data above, not the real file)
- **A live Angel One SmartAPI forum thread (Feb 2025) reports
  `getLtpData` rejecting a correctly-BFO'd SENSEX request that the
  identical code path handles fine for NIFTY** — unresolved as of
  that thread; may or may not still apply
- **A live Upstox community forum thread (Oct 2025) reports BSE_FO
  order-placement calls returning HTTP 404 while the identical NSE_FO
  call works** — this project never places orders (alert-only), so
  this specific endpoint isn't used here, but it's a signal that
  BSE_FO support on Upstox's API is newer/less exercised than NSE_FO's

**Run this against your own account first and watch the logs closely
before trusting SENSEX alerts unattended.** If it doesn't work,
removing `"SENSEX"` from `config.SYMBOLS` reverts every behavior
change above with no other edits needed.

### Same caveat as MIDCPNIFTY, plus liquidity

`MIN_OI` / `MIN_OPTION_VOLUME` / `MIN_PREMIUM` / `MAX_SPREAD_PCT` are
still the same NIFTY/BANKNIFTY-tuned globals, now shared across four
symbols instead of three. SENSEX's options liquidity is generally
thinner than NIFTY's even at reasonable strikes — watch the `SCORE`
log lines for this symbol specifically; "SENSEX never alerts" may mean
the shared threshold doesn't fit it, not that no setups exist.

## BTST (Buy Today, Sell Tomorrow) — overnight module (added 2026-07-23)

A **separate, opt-in** module for carrying options positions
overnight — NIFTY/BANKNIFTY + a small starter list of liquid stocks.
Built alongside the intraday system, not inside it: no existing file
(`state.py`, `tracker.py`, `scanner.py`, `main.py`, `config.py`,
`telegram_notify.py`) was modified to add this. The intraday pipeline
has zero knowledge this module exists, and a bug in BTST cannot break
same-day tracking.

### Why it's a separate module, not a mode of the existing one

The intraday system's core assumptions don't hold overnight:
`state.py` creates one fresh JSON file per calendar day and
`tracker.py` force-closes everything at market close — both correct
for a same-day hold, both wrong for a position meant to survive to
the next trading day. Reusing them would have meant changing their
behavior for every intraday trade too. Instead:

- **State**: `btst_state.py` writes to its own subdir (`btst/<entry-
  date>.json` on the same `state` branch) via `daily_cache.py`'s
  existing git plumbing — no new git logic, no shared files with the
  intraday state.
- **Detection**: `btst_scanner.py` reuses `scanner.score_strike()`
  completely unmodified for the actual signal detection (EMA/VWAP/OI/
  liquidity gates + the Trade Score Engine) — only the SL/target
  levels and the entry filters around it are BTST-specific.
- **Levels**: `btst_levels.py` is plain percentage-of-premium math
  (25% SL / 20% T1 / 40% T2 of entry premium by default), deliberately
  NOT built on `levels.py`'s ATR engine — that engine is calibrated
  for a 5–60 minute hold, and an 18-hour overnight window needs
  meaningfully more room before normal noise stops false-triggering a
  same-day-tight stop.

### Lifecycle

```
Evening (btst_main.py --entry, within BTST_ENTRY_WINDOW_HHMM):
  scan BTST_SYMBOLS -> score via scanner.score_strike() -> apply
  BTST_MIN_TRADE_SCORE floor + min-days-to-expiry + earnings blackout
  -> open position(s), status OPEN, entered at the EOD quote

Next trading day (btst_main.py --exit, run repeatedly through the day):
  OPEN -> GAP_SL_HIT     (exit day's first candle already gapped past SL)
  OPEN -> T1_HIT -> T2_HIT
  OPEN -> SL_HIT          (SL touched intraday, not a gap)
  OPEN -> FORCE_EXIT      (neither hit by BTST_FORCE_EXIT_HHMM)
  OPEN -> AMBIGUOUS       (SL and a target in the same candle)
```

`run_exit_step()` deliberately re-checks the last
`BTST_LOOKBACK_TRADING_DAYS` (5) trading days' files, not just
yesterday's — a single missed/failed exit-workflow run can't
permanently orphan an open position with real money in it.

### Stock-specific safety: earnings/event blackout

Overnight gaps on individual stocks are far more often driven by a
scheduled event (results, board meeting) than index-level gaps are.
`btst_earnings_guard.BLACKOUT_DATES` is a manual dict, same
"deliberately not a live API call" posture as `holiday_guard.py`'s own
movable-holiday list (see that file's docstring for why). **It ships
empty** — every BTST stock is unconditionally skipped
(`is_configured()` returns `False`) until a human populates it from
NSE's event calendar. This is intentional: an unpopulated list is
treated as "never checked", never as "confirmed safe".

### Probability / confidence — deliberately not shown for BTST

`btst_scanner.py` carries over `trade_score`/`grade` from
`score_strike()`'s output, but **not** `probability_pct` /
`confidence_score` / `expected_rr`. Those numbers are a function of
`trade_score`, but `trade_score` itself partly reflects
`risk_reward_strength()` computed against the *intraday* ATR-based SL/
target distances, before `btst_scanner.py` overwrites them with the
wider BTST percentage-based levels — so the number would describe a
risk/reward shape this position no longer has. Showing it would be
more misleading than not showing it. See `btst_main.
new_position_record()`'s docstring for the full reasoning if this
needs revisiting later — a real BTST probability model needs its own
calibration against BTST's actual SL/T1/T2 percentages and live
overnight outcomes, not a reuse of the intraday number.

### What's NOT backtested / needs live validation

Same honesty this README already applies to `config.py`'s intraday
weights: **`BTST_SL_PCT` / `BTST_T1_PCT` / `BTST_T2_PCT` (25%/20%/40%)
are a starting estimate, not backtested.** Validate against a few
weeks of real overnight moves before trusting them with size.

### What you still need to do before this touches real money

1. **Populate `btst_earnings_guard.BLACKOUT_DATES`** for every symbol
   in `btst_config.BTST_STOCK_SYMBOLS`, from NSE's corporate-
   announcements/event calendar. Every stock is skipped until you do.
2. **Wire `btst_main.py` into your EXISTING `trader.yml` job as extra
   steps — do NOT give it its own cron workflow.** `trader.yml`
   already runs every 5 min through 09:15-15:30 IST, which fully
   covers both BTST's entry window (15:00-15:20, inside `trader.yml`'s
   own Phase 3) and its exit-tracking window. A standalone
   `btst-exit.yml` on a similar cadence would run ~91 times/day on top
   of what's already close to the free 2,000-min/month tier (see
   "GitHub Actions minutes budget" above) — roughly **1,500-1,840
   extra minutes/month**, likely pushing total usage to 3,200-3,900
   min/month. Add two steps to `trader.yml`'s existing job instead
   (after the main `python scripts/main.py` step), reusing the same
   checkout/`setup-python`/secrets already in that job — this costs
   only the few extra seconds `btst_main.py` itself takes to run, not
   a second runner spin-up:
   ```yaml
   - name: Run BTST entry scan
     run: python scripts/btst_main.py --entry
   - name: Run BTST exit tracking
     run: python scripts/btst_main.py --exit
   ```
   Both are cheap no-ops outside their respective windows
   (`_in_entry_window()` / `run_exit_step()`'s own checks), so running
   them on every `trader.yml` tick is safe. `trader.yml` wasn't part
   of this repo export, so this couldn't be wired in directly here —
   add those two steps to your actual file.
3. **Live-validate the SL/T1/T2 percentages** against real overnight
   gaps for a few weeks before increasing size or widening
   `BTST_STOCK_SYMBOLS` beyond the current 10-name starter list.

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

## Dashboard API (removed 2026-10-31)

The optional read-only FastAPI dashboard (`scripts/app/backend/`,
`scripts/run_dashboard_api.py`, `requirements-dashboard.txt`) and the
`production/` monitoring modules and Part 7 Replay/Analytics engine that
only it used were removed at the owner's request -- no workflow ran
them. `git log` has the last version if it is ever needed again.

## Quant Intelligence Engine (Part 11, merged)

`scripts/app/engines/quant_intelligence/` is an independent
probability/signal-quality/regime/risk/pattern/learning/explainability
engine, merged in as a plug-in of the existing Decision Engine
(`scripts/app/engines/decision_engine/quant_adapter.py`). It runs as
the final step of `decision_engine.evaluate()`, reusing data the
scanner already computed each cycle -- no duplicate fetches or
recalculation. By default it's additive/visible only
(`config.QUANT_INTELLIGENCE_INFLUENCE_ENABLED = False`); any failure
degrades gracefully without affecting the rest of the Decision Object.
See `docs/QUANT_INTELLIGENCE_INTEGRATION.md` for the full architecture
diagram, configuration guide, known limitations, and manual setup
steps, and `scripts/app/engines/quant_intelligence/README.md` for the
engine's own internals.

## News Intelligence Engine (Part 11.5, merged)

`scripts/app/engines/news_intelligence/` is an independent news
collector/sentiment/impact engine (NSE/BSE/RBI/SEBI announcements +
market RSS feeds), merged in as a second plug-in of the existing
Decision Engine (`scripts/app/engines/decision_engine/news_adapter.py`),
running immediately after Quant Intelligence in
`decision_engine.evaluate()`. A single synchronous news-collection
cycle runs once per scan invocation (see
`docs/NEWS_INTELLIGENCE_INTEGRATION.md` for why it's synchronous
rather than a background scheduler); scoring itself is a fast in-memory
cache read, no network I/O per trade. By default it's additive/visible
only (`config.NEWS_INTELLIGENCE_INFLUENCE_ENABLED = False`); any
failure degrades gracefully without affecting the rest of the Decision
Object, including Quant Intelligence's own output. See
`docs/NEWS_INTELLIGENCE_INTEGRATION.md` for the full architecture
diagram, configuration guide, dependency notes, and manual setup
steps, and `scripts/app/engines/news_intelligence/README.md` for the
engine's own internals.
