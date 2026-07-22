"""
config.py
---------
Every tunable threshold in the system lives here, in one place, so
tuning quality/risk behavior never means hunting through scanner.py,
state.py, tracker.py, heartbeat_check.py for a scattered magic number.

What's deliberately NOT here:
- NSE_HOLIDAYS_2026 (holiday_guard.py) -- this is calendar DATA, not a
  tunable threshold. Moving it here wouldn't make it easier to find
  (holiday_guard.py is exactly where you'd look for it) and would mix
  "things you tune" with "things you look up once a year."
- UNDERLYING_INSTRUMENT_KEYS / VWAP_INSTRUMENT_KEYS -- these are
  environment/account-specific instrument identifiers, not risk or
  quality thresholds. They stay in scanner.py next to the code that
  uses them, clearly marked TODO.
- TERMINAL_STATUSES (main.py) -- this is a state-machine contract
  (tracker.py's status vocabulary), not a tunable number. Changing it
  isn't "tuning," it's changing the state machine itself.
"""

# --- Risk management (state.py) ---
# Circuit breaker: stop new alerts after this many SL hits IN A ROW
# (a T2 win resets the streak). Protects capital during a bad regime;
# doesn't by itself improve accuracy.
MAX_CONSECUTIVE_SL = 2

# Cap on simultaneously open trades (WAITING_ENTRY + ACTIVE + T1_HIT).
# Mostly a backstop now that only one best-ranked setup opens per run.
MAX_CONCURRENT_TRADES = 4

# --- Session windows (scanner.py) ---
# When NEW setups can be scanned for. Outside these windows,
# detect_setups() returns [] immediately (still tracks open trades).
SESSION_WINDOWS = [
    ((9, 30), (10, 35)),
    ((10, 35), (11, 35)),
    ((13, 45), (15, 10)),
]

# --- Symbols + strike spacing (scanner.py) ---
# STRIKE_STEP["MIDCPNIFTY"] = 25 added 2026-07-17 based on the
# historically-cited value in trader discussions -- NOT independently
# verified against a live NSE/broker contract spec (web research this
# session found MIDCPNIFTY's expiry/lot-size history clearly, but
# strike-interval sources were too inconsistent to trust). NSE has
# revised strike intervals for other indices as recently as Nov 2025
# (NIFTY's own monthly interval changed from 100 to 25), so this is
# exactly the kind of value that can go stale silently. CONFIRM this
# against an actual downloaded instrument file (filter instrument_type
# OPTIDX/name=MIDCPNIFTY, diff consecutive strike_price values) before
# trusting alerts on this symbol -- see the liquidity note below too.
STRIKE_STEP = {"NIFTY": 50, "BANKNIFTY": 100, "MIDCPNIFTY": 25}

# How many strikes on EACH side of ATM to evaluate. STRIKE_RANGE=1
# (the default -- unchanged from before this was configurable)
# evaluates exactly ATM-1, ATM, ATM+1, same as the original ITM/ATM/OTM
# behavior. STRIKE_RANGE=2 evaluates ATM-2..ATM+2 (5 strikes), etc.
# Whichever single strike scores best WITHIN each of the three
# ITM/ATM/OTM buckets is the one that advances to the existing
# cross-bucket ranking -- the "moneyness" field downstream (Trade IDs,
# suppression, Telegram alerts) is always still exactly one of
# "ITM"/"ATM"/"OTM", never a specific offset number, so nothing
# downstream needed to change for this. Increase cautiously: each
# extra step roughly doubles per-symbol scanner API calls per run.
STRIKE_RANGE = 1

# MIDCPNIFTY added 2026-07-17. Confirmed (NSE's own MIDCPNIFTY F&O
# page, checked this session): NSE, monthly-only since the Nov-2024
# SEBI weekly-consolidation rule, expiry = last Tuesday of the month
# (holiday -> previous trading day) -- same rule get_current_expiry()
# in both broker files already applies generically, so no special-
# casing was needed there. NOT independently confirmed: the strike
# interval hardcoded in STRIKE_STEP above, and MIN_OI/MIN_OPTION_VOLUME/
# MIN_PREMIUM/MAX_SPREAD_PCT below are still NIFTY/BANKNIFTY-tuned
# globals shared across all symbols -- MIDCPNIFTY's genuinely thinner
# liquidity may fail the liquidity_ok hard gate far more often than
# the other two symbols, which would look like "MIDCPNIFTY never
# alerts" but actually be "the shared threshold is wrong for it", not
# a real absence of setups. Watch the SCORE log lines for this symbol
# specifically before concluding either way.
#
# SENSEX remains deferred -- BSE (not NSE), different instrument-key
# exchange prefix, thinner liquidity needing its own thresholds; see
# README's original "Explicitly deferred" section for the details.
SYMBOLS = ["NIFTY", "BANKNIFTY", "MIDCPNIFTY"]

# --- Mid/Smallcap stock symbols ---
# RENAMED from STOCK_SYMBOLS (2026-07-17) to FALLBACK_STOCK_WATCHLIST
# as of Phase 1 (opportunity_score.py, 2026-07-22): this fixed
# 15-stock list is no longer the primary source of which stocks get
# scanned -- see refresh_symbols_for_today() below, which populates
# STOCK_SYMBOLS from today's computed ~100-stock-universe opportunity
# shortlist instead. This list now only matters on a day the
# shortlist cache is missing (build_opportunity_shortlist.py hasn't
# run, or failed) -- STOCK_SYMBOLS falls back to exactly this list on
# that day, so today's scan degrades to the OLD fixed-15-stock
# behavior rather than scanning nothing.
#
# UNVERIFIED against the CURRENT NSE F&O-eligible stock list -- NSE
# revises which stocks carry derivatives EVERY QUARTER (SEBI's
# liquidity-based inclusion/exclusion review), so a symbol here can
# drop out of F&O entirely with no code-level warning. CONFIRM every
# one of these is still F&O-active (check the broker's instrument
# master for instrument_type OPTSTK/FUTSTK rows with this name)
# before this runs live -- same caution already asked of MIDCPNIFTY's
# strike step and SENSEX's deferral above. This caveat now applies
# equally to universe.MASTER_UNIVERSE (Phase 1's ~100-stock list) --
# see that file's own comment.
FALLBACK_STOCK_WATCHLIST = [
    "TATASTEEL", "HINDALCO", "VEDL", "JINDALSTEL", "IDEA",
    "SAIL", "NATIONALUM", "NMDC", "BHEL", "FEDERALBNK",
    "BANKBARODA", "PFC", "RECLTD", "ASHOKLEY", "MOTHERSON",
]

# Per-stock strike interval. ALSO UNVERIFIED -- unlike NIFTY/BANKNIFTY
# (which rarely change), individual stock strike intervals get
# revised by NSE more often as the underlying price moves, and are
# NOT one-size-fits-all across stocks the way the index steps are.
# CONFIRM each against a downloaded instrument file (filter
# instrument_type OPTSTK, name=<symbol>, diff consecutive
# strike_price values) before trusting alerts on these -- exactly the
# verification MIDCPNIFTY's STRIKE_STEP entry above still needs too.
#
# As of Phase 2 (instrument_master.py), this dict is a FALLBACK ONLY
# -- option_chain.get_atm_strike() and scanner.resolve_strike_candidates()
# both try instrument_master's real listed strikes first and only
# fall back to this fixed-step arithmetic if that has no data for the
# symbol today. Kept scoped to the original 15 FALLBACK_STOCK_WATCHLIST
# names on purpose: a symbol Phase 1 shortlists that ISN'T in here has
# no safe fallback step (option_chain.get_atm_strike() raises
# ConfigurationError for that symbol on a bad cache day, which
# detect_all_setups()'s per-symbol try/except turns into a clean skip
# -- see that function's docstring), rather than guessing a step for a
# stock nobody has verified one for.
STOCK_STRIKE_STEP = {
    "TATASTEEL": 5, "HINDALCO": 10, "VEDL": 10, "JINDALSTEL": 20, "IDEA": 0.5,
    "SAIL": 2.5, "NATIONALUM": 2.5, "NMDC": 2.5, "BHEL": 5, "FEDERALBNK": 2.5,
    "BANKBARODA": 5, "PFC": 10, "RECLTD": 10, "ASHOKLEY": 5, "MOTHERSON": 2.5,
}
STRIKE_STEP.update(STOCK_STRIKE_STEP)

# Stock options trade at far lower OI/volume/premium than NIFTY/
# BANKNIFTY -- applying MIN_OI/MIN_OPTION_VOLUME/MIN_PREMIUM above
# unmodified would reject nearly every stock strike outright.
# filters.liquidity_ok() uses these instead for any symbol in
# STOCK_SYMBOLS, falling back to the index defaults otherwise. Applies
# equally to any of Phase 1's dynamically shortlisted stocks, not just
# the FALLBACK_STOCK_WATCHLIST names -- filters.py checks membership
# in STOCK_SYMBOLS (the live, refreshed list), not this fixed list.
# THESE STARTING VALUES ARE A GUESS, not backtested -- watch the
# SCORE log lines for these symbols the same way ENTRY_SCORE's
# comment above describes doing for the weighted-score cutoff, and
# tune from there.
STOCK_MIN_OI = 5_000
STOCK_MIN_OPTION_VOLUME = 500
STOCK_MIN_PREMIUM = 2

# --- Phase 1 (opportunity_score.py) ---
INDEX_SYMBOLS = list(SYMBOLS)  # ["NIFTY", "BANKNIFTY", "MIDCPNIFTY"] at this point -- captured before STOCK_SYMBOLS is appended below

TOP_N_STOCKS = 35  # midpoint of the spec's "30-40 stocks" -- tune from opportunity-shortlist Telegram logs

# Each ceiling is "the input value that earns the FULL weight for that
# factor" -- e.g. OPP_SCORE_RVOL_CEILING=3.0 means 3x average volume
# already earns full marks on the Relative Volume factor; going higher
# doesn't earn more. NONE OF THESE ARE BACKTESTED -- same "starting
# guess, tune from logs" caveat as STOCK_MIN_OI above.
# build_opportunity_shortlist.py's Telegram summary logs the top 10
# scores daily specifically so these are easy to sanity-check over time.
OPP_SCORE_RVOL_CEILING = 3.0        # 3x the 20-day average volume
OPP_SCORE_GAP_CEILING = 0.03        # 3% open-vs-prev-close gap
OPP_SCORE_ATR_CEILING = 0.025       # ATR = 2.5% of previous close
OPP_SCORE_MOM_CEILING = 0.08        # 8% five-trading-day price change
OPP_SCORE_OI_CEILING = 0.15         # 15% OI change day-over-day
OPP_SCORE_LIQUIDITY_CEILING = 500_000_000  # ₹50 crore 20-day avg daily traded value
OPP_SCORE_VWAP_CEILING = 0.02       # 2% previous-close distance from previous VWAP
OPP_SCORE_RANGE_CEILING = 0.04      # 4% previous-day (high-low)/close

# Sums to 100. Gap-Up/Gap-Down and Price-Change% from the original
# spec were collapsed into one "gap" factor (see the Phase 1 plan
# document's "why these nine and not the full list" section) --
# scoring both separately would silently double-count the same
# "this stock is moving" signal under two names.
OPPORTUNITY_SCORE_WEIGHTS = {
    "rvol": 20, "gap": 15, "atr": 15, "momentum": 10, "oi_change": 10,
    "sector": 10, "liquidity": 10, "vwap_dist": 5, "prev_range": 5,
}

# Live values -- start as the safe, no-I/O fallback (see
# refresh_symbols_for_today()'s docstring for why `import config`
# itself must never touch the state branch). Every module that does
# `from config import SYMBOLS` / `from config import STOCK_SYMBOLS` at
# import time binds to THESE list objects; refresh_symbols_for_today()
# mutates them in place (.clear()+.extend()), never reassigns, so
# every existing reference sees the update with no other code needing
# to change -- see that function's docstring for the full reasoning.
STOCK_SYMBOLS = list(FALLBACK_STOCK_WATCHLIST)
SYMBOLS = SYMBOLS + STOCK_SYMBOLS


def refresh_symbols_for_today() -> list:
    """
    Phase 1. Swaps STOCK_SYMBOLS/SYMBOLS from the safe import-time
    fallback to today's real opportunity shortlist (top TOP_N_STOCKS
    scored stocks out of universe.MASTER_UNIVERSE), if one has already
    been computed and cached today by build_opportunity_shortlist.py's
    premarket run.

    MUST be called by any entry point that scans (main.py does, right
    after state.ensure_state_branch_checked_out() and before the
    scanner runs) -- and MUST NOT be called at `import config` time.
    Reason: config.py is imported very early and very widely (by
    scanner.py directly, and by main.py before main.py ever calls
    ensure_state_branch_checked_out()) -- reading the state branch
    that early would either hit a not-yet-cloned directory (silently
    always falling back, defeating Phase 1 for every run) or force
    every script that merely imports config -- including
    config_validation.py and one-off diagnostics that have nothing to
    do with trading -- to eagerly clone a git branch just to import a
    config module. Keeping `import config` itself free of I/O, and
    making the state-branch-dependent refresh an explicit separate
    call, avoids both.

    Mutates the STOCK_SYMBOLS/SYMBOLS list objects IN PLACE rather
    than rebinding config.STOCK_SYMBOLS/config.SYMBOLS to new lists,
    because scanner.py/option_chain.py/filters.py all already did
    `from config import SYMBOLS` (or STOCK_SYMBOLS) at their own
    import time, which binds THEIR name to the SAME list object
    config.py created -- rebinding config's own attribute to a new
    list would leave every one of those already-bound references
    pointing at the stale fallback forever. In-place mutation means
    every existing reference sees today's real shortlist with zero
    other files needing to change -- this IS what "keep backward
    compatibility" requires here.

    Returns the (mutated in place) SYMBOLS list for convenience.
    """
    import daily_cache
    from datetime import datetime, timezone, timedelta
    IST = timezone(timedelta(hours=5, minutes=30))
    today = datetime.now(IST).strftime("%Y-%m-%d")

    cache = daily_cache.DailyCache("opportunity")
    payload = cache.read(today)
    if payload is None or not payload.get("shortlist"):
        # No shortlist yet today (premarket step hasn't run, or
        # failed) -- stay on whatever's already in place (the
        # FALLBACK_STOCK_WATCHLIST set at import time). Degrading to
        # the OLD fixed-15-stock behavior, not to zero stocks, is the
        # entire point of this fallback -- see FALLBACK_STOCK_WATCHLIST's
        # comment above.
        return SYMBOLS

    shortlist_symbols = [row["symbol"] for row in payload["shortlist"]]
    STOCK_SYMBOLS.clear()
    STOCK_SYMBOLS.extend(shortlist_symbols)
    SYMBOLS.clear()
    SYMBOLS.extend(INDEX_SYMBOLS + STOCK_SYMBOLS)
    return SYMBOLS

# --- Regime detection (scanner.py) ---
EFFICIENCY_TRENDING_THRESHOLD = 0.30
ATR_VOLATILE_PCT_THRESHOLD = 0.006  # ATR > 0.6% of spot = volatile session

REGIME_SCORE_PENALTY = {
    "TRENDING": 0,
    "SIDEWAYS": 8,
    "VOLATILE": 15,
}

# --- Grading thresholds (scanner.py) ---
# Internal 0-100 quality score -> external grade. Below the lowest
# threshold, compute_grade() returns "Reject" and it never reaches Telegram.
# Two-tier scheme (replaces the earlier A+/A/B+/B four-tier one):
# ENTRY_SCORE is the floor to alert on at all; STRONG_ENTRY is the
# higher-conviction tier above it.
# Two-tier scheme (replaces the earlier A+/A/B+/B four-tier one):
# ENTRY_SCORE is the floor to alert on at all; STRONG_ENTRY is the
# higher-conviction tier above it.
#
# Lowered from 75 to 65 on 2026-07-17: quality_gate() in filters.py
# used to require ALL 6 directional/confluence signals (not just the
# 3 hard risk gates) to be simultaneously True, which meant this
# weighted score was always either 100 (everything passed the old
# all-or-nothing gate) or the setup never reached scoring at all --
# 26 consecutive live scan cycles produced zero alerts. Once that
# gate was split (hard risk gates vs. weighted signal score), this
# threshold is the real, meaningful cutoff. Revisit after a couple of
# weeks of live data via the daily-summary win-rate check.
# Lowered from 65 to 60 on 2026-07-17: testing whether near-miss
# strikes (score 60-64) are common enough to be worth alerting on,
# after several consecutive live cycles produced zero alerts. NOTE:
# this only helps candidates that already clear the 3 hard risk gates
# in filters.quality_gate() (liquidity/delta/theta) -- if those are
# what's rejecting every strike (check the GATE log lines
# scanner.py's score_strike() now prints), lowering this floor won't
# change anything, since gate-failing strikes never reach the score
# stage at all. Revisit again after a week of live data via the
# daily-summary win-rate check -- a lower floor means more alerts but
# each one is, by definition, lower-conviction.
ENTRY_SCORE = 60
STRONG_ENTRY = 85

GRADE_THRESHOLDS = [
    (STRONG_ENTRY, "Strong Entry"),
    (ENTRY_SCORE, "Entry"),
]

CONFIDENCE_DISPLAY = {
    "Strong Entry": ("★★★★★", "Very High"),
    "Entry":        ("★★★☆☆", "Moderate"),
}

# --- Phase 3: Professional Trade Score Engine (trade_score.py) ---
# Replaces the binary strengths (1.0/0.0 per factor) grading.py's
# example_quality_score() used -- these are now CONTINUOUS 0-1 inputs
# computed from data already gathered in scanner.score_strike().
# Weights sum to 100, per the phase-3 spec's example distribution.
# RSI and PCR were explicitly asked for in the spec but are NOT
# calculated anywhere in this codebase (RSI is deliberately excluded
# per the V2.0 spec; PCR was never implemented) -- rather than bolt on
# two new indicators, these 10 factors are built entirely from
# indicators already computed elsewhere (EMA, VWAP, ATR/regime,
# OI build, liquidity, volume, 15-min MTF confirmation, R:R, and a
# broad-index (NIFTY) efficiency-ratio read for "Market Strength").
TRADE_SCORE_WEIGHTS = {
    "trend": 20,            # efficiency ratio of THIS symbol's 15-min candles (trend cleanliness)
    "volume": 15,           # option volume vs. the liquidity engine's own minimum, continuous
    "oi": 15,               # OI build % since last reading, continuous
    "momentum": 10,         # signed 15-min price momentum in the trade's direction
    "vwap": 10,             # signed distance from futures VWAP in the trade's direction
    "ema": 10,              # EMA9/21 spread as % of price (how cleanly separated, not just sign)
    "risk_reward": 5,       # target distance / stop distance vs. a 1.5 baseline
    "liquidity": 5,         # how far OI/volume clear the minimums + how tight the spread is
    "market_strength": 5,   # NIFTY's own 15-min efficiency ratio -- broad-tape conviction proxy
    "htf": 5,               # 15-min MTF confirmation (binary, same signal quality_gate() doesn't gate on alone)
}
assert sum(TRADE_SCORE_WEIGHTS.values()) == 100, "TRADE_SCORE_WEIGHTS must sum to 100"

# Ceilings: the raw value at which a continuous factor's strength hits
# 1.0 (100%). Same "clamp(x / ceiling, 0, 1)" convention as
# OPP_SCORE_*_CEILING in opportunity_score.py -- tune from live SCORE
# log lines the same way, not backtested yet.
TS_EMA_SPREAD_CEILING_PCT = 0.006     # EMA9/21 spread >= 0.6% of price -> full strength
TS_MOMENTUM_CEILING_PCT = 0.01        # 1% signed 15-min move -> full strength
TS_VWAP_DIST_CEILING_PCT = 0.004      # 0.4% signed distance from futures VWAP -> full strength
TS_OI_BUILD_CEILING_PCT = 0.04        # 2x OI_BUILD_THRESHOLD_PCT (0.02) -- full strength at 2x the
                                       # hard OI-build gate. Kept as a literal, not a reference to
                                       # OI_BUILD_THRESHOLD_PCT below, since this block sits above
                                       # where that constant is defined further down this file -- if
                                       # you retune OI_BUILD_THRESHOLD_PCT, update this to match.
TS_VOLUME_CEILING_MULT = 3.0          # option volume >= 3x the liquidity-engine minimum -> full strength
TS_LIQUIDITY_CEILING_MULT = 2.0       # OI/volume >= 2x their minimums -> full strength on those sub-parts
TS_RR_BASELINE = 1.5                  # target-distance/SL-distance >= this -> full R:R strength

# New Trade Grade vocabulary matching the phase-3 spec's example output
# (A+/A/B+/B) -- distinct from the old Strong Entry/Entry GRADE_THRESHOLDS
# above, which stays as-is for backward compatibility (nothing besides
# the old grading.py path reads it anymore, but it isn't deleted).
TRADE_SCORE_GRADE_THRESHOLDS = [
    (90, "A+"),
    (80, "A"),
    (70, "B+"),
    (60, "B"),
]

# Backward-compat display fields (confidence_stars/confidence_label still
# populated on every trade dict, same as before) -- just re-keyed to the
# new A+/A/B+/B grade vocabulary instead of the old Strong Entry/Entry one.
CONFIDENCE_STARS_BY_TRADE_GRADE = {
    "A+": ("★★★★★", "Very High"),
    "A":  ("★★★★☆", "High"),
    "B+": ("★★★☆☆", "Moderate"),
    "B":  ("★★☆☆☆", "Fair"),
}

# --- Liquidity engine (scanner.py) ---
MIN_OI = 15_000
MIN_OPTION_VOLUME = 5_000
MIN_PREMIUM = 60             # reject strikes priced below this (rupees)
MAX_SPREAD_PCT = 0.03        # bid-ask spread must be <= 3% of premium.
                              # Percentage-of-premium, not absolute rupees --
                              # a flat rupee cap unfairly penalizes cheap
                              # OTM contracts and is too loose on expensive
                              # ITM ones. Liquid ATM NIFTY spreads run well
                              # under 0.15% of premium; Bank Nifty ATM
                              # spreads can widen to Rs 80-150 during high
                              # volatility, which only a %-based cap handles
                              # sensibly across that whole range. 3% keeps
                              # the gate selective without rejecting normal
                              # liquid strikes.


# --- Dynamic SL/target, ATR-based (scanner.py) ---
SL_ATR_MULT = 1.0
T1_ATR_MULT = 1.5
T2_ATR_MULT = 2.5

# Entry zone width scales with ATR, floored/capped so it's never
# unfillable (too narrow) or unreasonably wide (extreme volatility).
ENTRY_ZONE_ATR_FRACTION = 0.15  # total zone width = 15% of ATR-based premium move
MIN_ENTRY_ZONE_PCT = 0.01       # floor: at least 1% total width
MAX_ENTRY_ZONE_PCT = 0.05       # cap: at most 5% total width

OI_BUILD_THRESHOLD_PCT = 0.02

# --- Delta floors (scanner.py) ---
# Rejects deep-OTM "lottery ticket" strikes. ATM/ITM aren't subject to
# this floor -- their delta is naturally higher.
MIN_DELTA_OTM = 0.15

# Stricter OTM delta floor specifically near expiry, where theta decay
# dominates any directional signal.
EXPIRY_DAY_MIN_OTM_DELTA = 0.25
EXPIRY_DAY_THRESHOLD_DAYS = 1

# --- Tracker (tracker.py) ---
MARKET_CLOSE_HHMM = (15, 30)

# --- Daily summary (main.py) ---
# Moved from 15:25 to 15:31 on 2026-07-21 (per request) -- fires just
# after market close (15:30) instead of 5 minutes before it, so the
# summary reflects the fully-closed day rather than being sent while
# the last few minutes of trading could still move an open position.
# trader.yml already runs continuously through the day regardless of
# SESSION_WINDOWS (maybe_send_daily_summary() is called unconditionally
# every run, independent of session scanning), so this needed no
# workflow/schedule change -- only this time moved.
SUMMARY_HHMM = (15, 31)

# --- Heartbeat check (heartbeat_check.py) ---
# BUGFIX (comment, not value): this used to say "7-min trader cadence"
# -- but heartbeat.yml runs continuously across market hours,
# including during trader.yml's 11:35-13:45 lunch-gap phase, where the
# cadence is 15 min, not 7 (now 5). The LARGEST gap between trader
# runs is what determines the worst-case staleness gap heartbeat needs
# to tolerate without a false alarm -- so this must be sized off the
# slowest phase (15 min, unchanged), not the fastest one. 30+15=45
# with zero buffer was already this value before the 7->5 min change
# below; that change doesn't affect this number at all, since it only
# touches the two ALREADY-faster active-scanning phases.
STALE_THRESHOLD_MIN = 45  # 30-min heartbeat interval + 15-min lunch-gap trader cadence (worst case) -- see trader.yml
MARKET_OPEN_HHMM = (9, 15)
MARKET_CLOSE_HHMM_HEARTBEAT = (15, 30)
STARTUP_GRACE_MIN = 15    # skip staleness check for this long after market open

# --- Broker failover (broker_failover.py) ---
# Switch from Angel One (primary) to Upstox (fallback) after this many
# CONSECUTIVE failed calls to Angel One within a single run. Kept low
# (not e.g. 10) because every failed call already burns
# RETRY_MAX_ATTEMPTS retries on its own before counting as one
# failure here -- by the time this threshold is reached, Angel One
# has already failed (3 retries x this many) times, not just this many.
FAILOVER_CONSECUTIVE_FAILURE_THRESHOLD = 2

# A candle/quote is considered "stale" (and treated as a failure for
# failover purposes) if its timestamp is older than this many minutes
# during market hours -- catches a broker returning HTTP 200 with
# genuinely stale data (no error to retry on) rather than an outright
# failure.
STALE_DATA_THRESHOLD_MIN = 10

# --- Network retry (market_data.py, telegram_notify.py) ---
# Retry only transient failures (connection errors, timeouts, rate
# limits, momentary 5xx) -- never auth errors or other 4xx, which need
# a human, not a retry loop.
RETRY_MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 1.5
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

# --- Angel One historical-candle throttle (angelone.py) ---
# BUGFIX (live failure, 2026-07-21): retrying AFTER hitting Angel
# One's historical-candle rate limit wasn't enough -- confirmed live,
# the limit is sustained (every retry within the same call, and the
# next symbol's call right after, kept getting rate-limited too), not
# a one-off burst that clears itself. The real fix is to space our
# OWN outgoing requests further apart so we never trigger the limit
# in the first place. UNVERIFIED exact value -- Angel One doesn't
# publish a precise number for this specific endpoint as far as this
# comment's author could confirm; 1.0s (1 request/second) is a
# deliberately conservative starting point given repeated live
# failures even faster than that. If Angel One still rate-limits at
# this spacing, raise it further; if it never does, this can likely
# be lowered to speed up each run's total scan time.
ANGELONE_HISTORICAL_MIN_INTERVAL_SECONDS = 1.0
