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

# Master switch for the circuit breaker above. True (default) is the
# original, unchanged live-trading behaviour: MAX_CONSECUTIVE_SL SL
# hits in a row trips the breaker and stops new alerts for the rest of
# the day. Set to False ONLY for validation/performance-testing runs
# where you want the scanner to keep running (and keep generating
# alerts) for the entire session regardless of how many SL hits occur
# -- consecutive_sl_streak is still tracked and reported either way,
# this flag only controls whether crossing MAX_CONSECUTIVE_SL actually
# halts new alerts. See state.record_trade_outcome_for_streak().
STOP_AFTER_CONSECUTIVE_SL = True

# Cap on simultaneously open trades (WAITING_ENTRY + ACTIVE + T1_HIT).
# Mostly a backstop now that only one best-ranked setup opens per run.
MAX_CONCURRENT_TRADES = 4

# Minimum minutes that must pass after a symbol+direction's most recent
# alert (open OR already closed) before a new alert for that SAME
# symbol+direction can fire. has_open_trade() alone only blocks while a
# trade is still live -- without this, a trade that closes (T2/SL/EOD)
# could be immediately followed by a fresh alert on the same setup one
# scan later, which reads as a duplicate even though it's technically a
# new trade_id. 0 disables the cooldown (restores the old behavior).
DUPLICATE_ALERT_COOLDOWN_MINUTES = 15

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
STRIKE_STEP = {"NIFTY": 50, "BANKNIFTY": 100, "MIDCPNIFTY": 25, "SENSEX": 100}

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
# SENSEX added 2026-07-23 -- previously deferred (BSE, not NSE;
# different instrument-key exchange prefix). Broker support added in
# angelone.py (BFO exch_seg, auto-detected via _exchange_segment())
# and upstox.py (BSE_INDEX|SENSEX + a second BSE.json.gz instrument-
# master fetch merged with NSE's). SAME caveat as MIDCPNIFTY above,
# and then some: MIN_OI/MIN_OPTION_VOLUME/MIN_PREMIUM/MAX_SPREAD_PCT
# below are NIFTY/BANKNIFTY-tuned and SENSEX liquidity is thinner and
# less mature than NIFTY's even at reasonable strikes -- watch the
# SCORE log lines closely before trusting this symbol's alerts.
# UNVERIFIED LIVE (2026-07-23): the broker-layer BFO/BSE_FO code paths
# below have NOT been exercised against a live account -- this was
# built from Angel One/Upstox's own documentation and forum threads,
# not a live test run (see angelone.py's and upstox.py's module
# docstrings for specifics, including two DIFFERENT live forum reports
# of Angel One getLtpData and Upstox order-placement both rejecting
# valid-looking SENSEX/BFO/BSE_FO requests as of late 2025 -- run this
# against your own account and watch the logs closely before trusting
# it unattended). Remove "SENSEX" from this list to fall back to the
# previous NIFTY/BANKNIFTY/MIDCPNIFTY-only behavior with zero other
# changes needed.
SYMBOLS = ["NIFTY", "BANKNIFTY", "MIDCPNIFTY", "SENSEX"]

# FINNIFTY note (2026-07-24): the Institutional-upgrade spec's Stage 1
# lists FINNIFTY as one of the underlyings to analyse. It's deliberately
# NOT added here -- NSE discontinued FINNIFTY weekly F&O contracts as
# part of its 2024 index-derivatives rationalization, and neither
# broker integration in this codebase has a STRIKE_STEP/instrument
# mapping for it (see STRIKE_STEP above). Adding "FINNIFTY" to this list
# without live contracts behind it would just make every symbol-level
# call fail for it, every run. If NSE relists it, add a STRIKE_STEP
# entry and append it here -- same pattern MIDCPNIFTY and SENSEX
# followed above.

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

# --- Stock-universe validation mode (temporary, universe-reduction only) ---
# NOT the same thing as STOP_AFTER_CONSECUTIVE_SL above -- that flag
# changes risk-management behavior (circuit breaker). This flag ONLY
# changes WHICH stocks get scanned; it never touches entry/exit logic,
# scoring, risk management, BTST, Telegram, monitoring, or the
# dashboard. Purpose: cut broker API calls/failure surface way down
# for a validation run by scanning a small, known-liquid stock set
# instead of the full dynamic ~100-150 stock universe, while every
# index (config.SYMBOLS' index members, see INDEX_SYMBOLS below) and
# the BTST module (fully separate -- see btst_config.BTST_SYMBOLS)
# keep running exactly as configured.
#
# True  -> refresh_symbols_for_today() (and the import-time fallback
#          below) use VALIDATION_STOCK_UNIVERSE instead of the daily
#          opportunity shortlist / FALLBACK_STOCK_WATCHLIST, and skip
#          the shortlist-cache read entirely (one less daily_cache hit).
# False (default) -> unchanged original behavior: today's opportunity
#          shortlist if cached, else FALLBACK_STOCK_WATCHLIST.
#
# Flip back to False (or delete this block) to instantly restore the
# full stock universe -- nothing else needs to change.
STOCK_UNIVERSE_VALIDATION_MODE = True

# The 10 stocks scanned when STOCK_UNIVERSE_VALIDATION_MODE is True.
# Deliberately NOT added to STOCK_STRIKE_STEP below -- these are all
# large, liquid F&O names that instrument_master.py's live strike
# lookup resolves directly (see that dict's Phase 2 fallback-only
# note); if instrument-master data is ever missing for one of them on
# a given day, detect_all_setups()'s existing per-symbol try/except
# skips just that symbol cleanly, same as it does today for any other
# stock -- no new failure mode.
VALIDATION_STOCK_UNIVERSE = [
    "RELIANCE", "HDFCBANK", "ICICIBANK", "SBIN", "TCS",
    "INFY", "LT", "AXISBANK", "BHARTIARTL", "ITC",
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
# Updated 2026-07-22 from verify_stock_symbols.py's live CI run against
# both brokers' instrument masters -- 12 of 15 values below were stale
# (see the run's MISMATCH lines); HINDALCO/SAIL/FEDERALBNK already matched.
STOCK_STRIKE_STEP = {
    "TATASTEEL": 2.5, "HINDALCO": 10, "VEDL": 5, "JINDALSTEL": 10, "IDEA": 1.0,
    "SAIL": 2.5, "NATIONALUM": 5, "NMDC": 1.0, "BHEL": 2.5, "FEDERALBNK": 2.5,
    "BANKBARODA": 1.0, "PFC": 5, "RECLTD": 5, "ASHOKLEY": 2.5, "MOTHERSON": 0.5,
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
# Relaxed 2026-07-24 (Phase 4 "increase alert volume" pass): these were
# tuned tight enough that borderline-but-tradeable stock strikes (a hair
# under the old OI/volume floor, otherwise clean setups) were being
# rejected outright by filters.liquidity_ok(). liquidity_strength() in
# trade_score.py still scores the SOFT version of this on a 0-1 scale,
# so a strike that only just clears these floors still shows up as a
# weaker Liquidity contributor in the score breakdown -- this only
# changes the hard pass/fail line, not how good the trade LOOKS.
STOCK_MIN_OI = 4_000
STOCK_MIN_OPTION_VOLUME = 400
STOCK_MIN_PREMIUM = 2

# --- Phase 1 (opportunity_score.py) ---
INDEX_SYMBOLS = list(SYMBOLS)  # ["NIFTY", "BANKNIFTY", "MIDCPNIFTY"] at this point -- captured before STOCK_SYMBOLS is appended below

TOP_N_STOCKS = 20  # narrowed from 35 to 20 (2026-07-24) -- fewer, higher-liquidity
                    # names scanned per run means fewer broker calls and less
                    # exposure to the thinner end of the stock-options book;
                    # tune further from opportunity-shortlist Telegram logs

# --- Phase 5 (2026-07-24): dynamic F&O universe + momentum/liquidity prefilter ---
# Per an explicit request to widen the candidate pool beyond the ~112-name
# hand-curated MASTER_UNIVERSE: when True, opportunity_score.build_shortlist()
# sources its candidate pool from universe.fno_eligible_stock_symbols()
# (the REAL, exchange-wide F&O list, ~200-250 names, read live from the
# broker's instrument master) instead of universe.universe_symbols().
# That full pool is then cheaply pre-ranked on momentum + liquidity ALONE
# (mom_5d + adtv, reusing OPP_SCORE_MOM_CEILING/OPP_SCORE_LIQUIDITY_CEILING
# below) and cut down to DYNAMIC_UNIVERSE_TOP_N before the expensive
# per-symbol OI fetch and full 9-factor scoring run -- so widening the pool
# doesn't multiply the option-chain-fetch cost by 2x, only the cheap
# daily-candle prefilter step. Falls back to the static MASTER_UNIVERSE
# (and skips the prefilter) if the instrument-master lookup returns nothing.
USE_DYNAMIC_FNO_UNIVERSE = True
DYNAMIC_UNIVERSE_TOP_N = 150

# TOP_STOCK_COUNT is the requested config name for "how many stocks make
# today's SCAN_UNIVERSE" -- deliberately NOT a second independent
# variable. It's the same knob as TOP_N_STOCKS above (opportunity_score.
# build_shortlist() already slices scored[:TOP_N_STOCKS]); duplicating it
# under a second name would just create two numbers that can silently
# drift apart. Change TOP_N_STOCKS above; TOP_STOCK_COUNT always mirrors it.
TOP_STOCK_COUNT = TOP_N_STOCKS

# --- Phase 6 (2026-07-29): intraday dynamic re-ranking ---
# Extends Phase 1 (premarket opportunity_score.build_shortlist(), which
# still runs once before market open and seeds the day's SCAN_UNIVERSE)
# with periodic INTRADAY re-scoring of the SAME MASTER_UNIVERSE/dynamic
# F&O pool, so a stock that goes cold (or hot) after the open can be
# swapped out of (into) SCAN_UNIVERSE without any manual watchlist edit.
# See intraday_rerank.py for the run logic; this block is config only.
#
# ENABLE_INTRADAY_RERANK: master on/off switch. False -> today behaves
# exactly like before this change (one shortlist, computed premarket,
# used all day) -- flip to False to instantly revert to the old behavior
# with no other code changes needed.
ENABLE_INTRADAY_RERANK = True

# INITIAL_RANK_TIME: informational/documentation constant for WHEN the
# day's first SCAN_UNIVERSE becomes live. The actual first ranking is
# still opportunity_score.build_shortlist()'s premarket run (see
# build_opportunity_shortlist.py) -- trader.yml's first scan tick of the
# day (nominally 09:30, right after the first 15-minute candle closes)
# is simply the first run that PICKS UP that shortlist via
# config.refresh_symbols_for_today(). Kept as an explicit config value
# (rather than a bare comment) so a schedule change only needs this one
# edit, and so intraday_rerank.py can log/verify today's first scan tick
# landed at or after this time.
INITIAL_RANK_TIME = "09:30"

# RERANK_TIMES: the HH:MM (IST, 24h) marks at which SCAN_UNIVERSE is
# recomputed from a fresh, live re-score of the full universe/dynamic
# F&O pool. Every entry must land inside a trader.yml scan window (see
# that workflow's schedule comment) or it will simply be picked up by
# the next run after it, same as any other tick. Add/remove/reorder
# freely -- nothing else needs to change.
RERANK_TIMES = ["11:30", "13:30", "14:30"]

# A trader.yml run fires every 5 minutes in the active windows but only
# every 15 minutes during the 11:35-13:45 lunch gap -- RERANK_WINDOW_
# MINUTES is how close (in minutes, either side) a run's clock time must
# be to a RERANK_TIMES entry to count as THAT slot's rerank. 10 covers
# the widest (15-minute) run cadence with room to spare; a run further
# than this from every RERANK_TIMES entry does no reranking at all, just
# the normal scan with the currently-live SCAN_UNIVERSE.
RERANK_WINDOW_MINUTES = 10

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
STOCK_SYMBOLS = list(VALIDATION_STOCK_UNIVERSE) if STOCK_UNIVERSE_VALIDATION_MODE \
    else list(FALLBACK_STOCK_WATCHLIST)
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

    If STOCK_UNIVERSE_VALIDATION_MODE is True, this short-circuits
    before touching daily_cache at all -- see that flag's comment
    near VALIDATION_STOCK_UNIVERSE above.
    """
    if STOCK_UNIVERSE_VALIDATION_MODE:
        # Validation mode: always the fixed 10-stock validation
        # universe, every run, regardless of whether a shortlist is
        # cached today. Skips the daily_cache read below entirely --
        # one less moving part / API-adjacent call per run, which is
        # part of the point of validation mode. Indices (INDEX_SYMBOLS)
        # are untouched, so NIFTY/BANKNIFTY/MIDCPNIFTY/SENSEX keep
        # scanning exactly as configured.
        STOCK_SYMBOLS.clear()
        STOCK_SYMBOLS.extend(VALIDATION_STOCK_UNIVERSE)
        SYMBOLS.clear()
        SYMBOLS.extend(INDEX_SYMBOLS + STOCK_SYMBOLS)
        return SYMBOLS

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

# --- Market Snapshot (app/market/snapshot.py, added 2026-07-25) ---
# Bands efficiency_ratio()'s 0-1 reading (already computed by
# indicators.market_regime()) into Part 2's five trend-strength labels --
# highest-first, first match wins, same walking convention as every other
# threshold table in this file.
TREND_STRENGTH_BANDS = [
    (0.65, "Very Strong"),
    (0.45, "Strong"),
    (0.30, "Neutral"),   # matches EFFICIENCY_TRENDING_THRESHOLD above
    (0.15, "Weak"),
]  # below 0.15 -> "Very Weak" (the function's fallback)

VOLATILITY_SCORE_ATR_PCT_CEILING = 0.012  # ATR at 1.2% of spot -> volatility_score caps at 100
VOLATILITY_LEVEL_BANDS = [
    (ATR_VOLATILE_PCT_THRESHOLD, "High"),   # reuses the SAME 0.6% threshold market_regime() already uses for "VOLATILE"
    (0.003, "Normal"),
]  # below 0.3% -> "Low"

GAP_MIN_PCT = 0.002  # session-open vs. previous close must differ by >=0.2% to count as a genuine gap, not noise

# --- Price Action candlestick patterns (app/engines/early_breakout/
# price_action.py, added 2026-07-26 -- see that module's docstring for
# why only candlestick patterns, not full chart patterns, were built).
# Same "confirmation bonus, never a rejection reason" pattern as FVG/OI
# above -- an aligned pattern adds confidence, its absence never
# rejects an otherwise-qualifying setup. ---
DOJI_MAX_BODY_PCT = 0.10           # body <=10% of the candle's own range -> Doji
MARUBOZU_MIN_BODY_PCT = 0.90       # body >=90% of range -> Marubozu (almost no wicks)
PIN_BAR_MIN_WICK_TO_BODY_RATIO = 2.0   # the long wick must be >=2x the body -> Hammer/Shooting Star
PIN_BAR_MAX_OPPOSITE_WICK_PCT = 0.15   # the OTHER wick must be <=15% of range -- keeps it one-sided
MOMENTUM_CANDLE_MIN_BODY_PCT = 0.70    # body >=70% of range...
MOMENTUM_CANDLE_MIN_ATR_MULT = 1.0     # ...AND the full range itself >=1x ATR -> Momentum Candle
PRICE_ACTION_CONFIRMATION_BONUS = 3

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
# Lowered from 60 to 55 on 2026-07-24 (Phase 4 "increase alert volume"
# pass): with the 3-hard-gate design (liquidity/delta/theta) already in
# place, 55-59 setups are near-misses on the weighted score, not gate
# failures -- worth surfacing as low-tier "Watchlist" alerts (see
# trade_score.probability_label()) rather than discarded, since
# Telegram sends are separately gated at TELEGRAM_MIN_PROBABILITY below
# regardless of this floor. Revisit via the daily-summary win-rate
# check same as the last two changes to this constant.
ENTRY_SCORE = 55
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
#
# BUGFIX (2026-07-24, found during code review): this list used to
# bottom out at 60 ("B"), so score_strike()'s `if grade == "Reject":
# return None` silently discarded every 55-59 setup before it could
# ever reach ENTRY_SCORE's own 55 floor in detect_setups() --
# contradicting this exact file's Phase 4 "increase alert volume"
# comment on ENTRY_SCORE below, which explicitly says 55-59 setups
# are near-misses worth surfacing as low-tier "Watchlist" alerts, not
# gate failures. Adding the (ENTRY_SCORE, "Watchlist") tier here is
# what actually wires that documented intent into compute_grade() --
# ENTRY_SCORE is referenced (not a literal 55) so the two stay in
# sync if this floor is ever retuned again.
TRADE_SCORE_GRADE_THRESHOLDS = [
    (90, "A+"),
    (80, "A"),
    (70, "B+"),
    (60, "B"),
    (ENTRY_SCORE, "Watchlist"),  # ENTRY_SCORE is defined earlier in this
                                   # file (currently 55) -- referenced, not
                                   # duplicated, so retuning ENTRY_SCORE
                                   # automatically keeps this tier in sync.
]

# Backward-compat display fields (confidence_stars/confidence_label still
# populated on every trade dict, same as before) -- just re-keyed to the
# new A+/A/B+/B grade vocabulary instead of the old Strong Entry/Entry one.
# "Watchlist" added alongside the TRADE_SCORE_GRADE_THRESHOLDS bugfix above
# so a 55-59 setup gets a real star display instead of confidence_display()'s
# ("☆☆☆☆☆", "N/A") fallback.
CONFIDENCE_STARS_BY_TRADE_GRADE = {
    "A+":        ("★★★★★", "Very High"),
    "A":         ("★★★★☆", "High"),
    "B+":        ("★★★☆☆", "Moderate"),
    "B":         ("★★☆☆☆", "Fair"),
    "Watchlist": ("★☆☆☆☆", "Low"),
}

# --- Phase 4: probability bands + Telegram send gate (2026-07-24) ---
# Independent of TRADE_SCORE_GRADE_THRESHOLDS above (which grades the
# 0-100 trade_score) -- this bands trade_score.compute_probability_pct()'s
# output for display/transparency, and separately controls which of
# those bands actually reach Telegram. Every candidate that clears the
# ENTRY_SCORE gate still gets scored, logged, and tracked internally
# (so win-rate stats aren't skewed by what got messaged) -- this only
# gates the notification, per an explicit "only send Telegram alerts
# when probability >= 80%" requirement. Ordered highest-first;
# probability_label() in trade_score.py walks this the same way
# compute_grade() walks TRADE_SCORE_GRADE_THRESHOLDS.
PROBABILITY_BANDS = [
    (90, "Strong Institutional Setup"),
    (80, "High Probability Trade"),
    (70, "Potential Trade"),
    (60, "Watchlist"),
    (0,  "Ignore"),
]
# NOTE (2026-07-24, live bug fix): this NO LONGER gates whether
# NEW_TRADE_ALERT is sent -- see the note in main.py's run_scanner_step()
# for why (it was silently hiding the pre-alert for trades whose
# unconditional tracker events -- ENTRY_ACTIVATED/TARGET_HIT/SL_HIT --
# still went out regardless, confirmed live on 2026-07-24 where every
# trade that day was below this floor). Kept defined here since
# trade_score.probability_label()'s docstring still references it by
# name and PROBABILITY_BANDS' "High Probability Trade" tier (80) means
# the same thing conceptually -- just not wired to an actual gate
# anymore. Left in place rather than deleted in case a future call site
# wants a probability-based Telegram gate again; if you re-add one,
# prefer gating via ENTRY_SCORE (fewer trades tracked at all) so alerts
# and outcomes stay consistent -- see that same note for why.
TELEGRAM_MIN_PROBABILITY = 80

# --- Liquidity engine (scanner.py) ---
# Relaxed 2026-07-24 (Phase 4 "increase alert volume" pass, per an
# explicit "relax liquidity slightly" instruction): MIN_OI/
# MIN_OPTION_VOLUME lowered ~20%, MAX_SPREAD_PCT widened from 3% to
# 4%. These are still hard hygiene gates (an option book this thin or
# this wide can't be entered/exited cleanly regardless of signal
# quality) -- only the line moved, not the principle. The old, tighter
# values are the ones to revert to if live fills on the newly-admitted
# strikes turn out worse than the rest.
MIN_OI = 12_000
MIN_OPTION_VOLUME = 4_000
MIN_PREMIUM = 60             # reject strikes priced below this (rupees)
MAX_SPREAD_PCT = 0.04        # bid-ask spread must be <= 4% of premium (was 3%).
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

# BUGFIX (2026-07-24, live pattern -- see levels.dynamic_trade_levels()):
# the nearby-level cap on T2 is only meaningful when the level is a real
# distance AHEAD of price. Right at a fresh breakout, find_nearby_level()'s
# lookback often picks up the breakout candle itself as the "recent
# high/low", putting the level essentially AT the current price -- capping
# T2 down to almost nothing even though nothing is actually blocking the
# move. Below this many ATR of distance, the cap is skipped and the raw
# ATR-based T2 is used instead.
MIN_LEVEL_DISTANCE_ATR_MULT = 1.0

OI_BUILD_THRESHOLD_PCT = 0.02

# --- Delta floors (scanner.py) ---
# Rejects deep-OTM "lottery ticket" strikes. ATM/ITM aren't subject to
# this floor -- their delta is naturally higher.
MIN_DELTA_OTM = 0.15

# Stricter OTM delta floor specifically near expiry, where theta decay
# dominates any directional signal.
EXPIRY_DAY_MIN_OTM_DELTA = 0.25
EXPIRY_DAY_THRESHOLD_DAYS = 1

# --- Options Intelligence Engine (Part 5) -------------------------------
# app/engines/options_intelligence/ -- option chain / PCR / Max Pain /
# Greeks / IV / liquidity / premium / strike / expiry / momentum,
# consolidated into one Options Intelligence Score. This is layered
# ADDITIVELY onto the existing Trade Quality Gate (filters.py) and
# Trade Score Engine (trade_score.py) -- it does not replace or
# recompute either. See app/engines/options_intelligence/engine.py's
# module docstring for exactly how/where it plugs into
# scanner.score_strike(). Every threshold below is configurable, none
# hardcoded in the engine modules themselves.
OPTIONS_INTELLIGENCE_ENABLED = True    # master flag -- False fully restores pre-Part-5 behavior
OPTIONS_INTELLIGENCE_FILTER_ENABLED = False  # if True, a low Options Intelligence Score can reject a
                                              # setup that already passed the existing hard gate + Trade
                                              # Score floor. Defaults OFF: Part 5 ships purely additive
                                              # (extra logged/displayed fields on the trade dict only)
                                              # until its scores have been watched against a few days of
                                              # real outcomes -- flip this on when you're ready to let it
                                              # actually reject trades, same rollout posture the rest of
                                              # this file already uses for new engines.
OPTIONS_INTELLIGENCE_MIN_SCORE = 60    # 0-100 floor, only enforced when the filter flag above is True

# PCR Engine -- overall/strike-wise PCR bias bands
PCR_BULLISH_MAX = 0.7    # PCR <= this -> call-heavy / bullish bias
PCR_BEARISH_MIN = 1.3    # PCR >= this -> put-heavy / bearish bias

# Max Pain Engine
MAX_PAIN_DISTANCE_CEILING_PCT = 0.02  # spot >=2% away from max pain -> full Max Pain Score

# Open Interest Engine (reuses OI_BUILD_THRESHOLD_PCT above for build/unwind classification)
OI_CONCENTRATION_TOP_N_STRIKES = 3    # how many highest-OI strikes count as "OI concentration" per side

# Greeks Engine
GAMMA_RISK_CEILING = 0.002       # gamma (per point of underlying) at/above this -> full gamma-risk reading
THETA_DECAY_CEILING_PCT = 0.03   # |theta| as a fraction of premium, per day, at/above this -> full decay-risk reading
VEGA_EXPOSURE_CEILING = 5.0      # vega at/above this -> full IV-exposure reading

# Implied Volatility Engine
IV_ABNORMAL_LOW_PCT = 0.08    # IV below 8% (annualized) treated as a stale/garbage quote, not "cheap"
IV_ABNORMAL_HIGH_PCT = 1.20   # IV above 120% treated as a stale/illiquid quote, not "rich"
IV_PERCENTILE_LOOKBACK_READINGS = 20  # in-process IV history window per instrument_key (percentile/rank/trend)

# Premium Analysis Engine
PREMIUM_TIME_VALUE_MAX_PCT = 0.95  # extrinsic/time value at/above this fraction of premium (deep-OTM
                                    # "lottery ticket" pricing) -> Premium Score floors near zero

# Strike Selection / Expiry Analysis Engines
EXPIRY_WEEKLY_MAX_DAYS = 7    # days-to-expiry at/below this -> "Weekly" bucket for the Expiry Score

# Options Momentum Engine
MOMENTUM_HISTORY_READINGS = 3   # in-process readings kept per instrument_key (premium/OI/volume/IV momentum)

# Options Scoring Engine -- consolidated Options Intelligence Score weights. Must sum to 100.
OPTIONS_INTELLIGENCE_WEIGHTS = {
    "oi": 15,
    "pcr": 10,
    "max_pain": 10,
    "greeks": 15,
    "iv": 10,
    "liquidity": 15,
    "premium": 10,
    "strike": 5,
    "expiry": 5,
    "momentum": 5,
}

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
# Switch from the primary broker to the fallback broker (Upstox
# primary, Angel One fallback as of 2026-07-29 -- see market_data.py's
# module docstring for the full swap history and reasoning, including
# why Angel One is staying off primary going forward) after this many
# CONSECUTIVE failed calls to the primary within a single run. Kept
# low (not e.g. 10) because every failed call already burns
# RETRY_MAX_ATTEMPTS retries on its own before counting as one
# failure here -- by the time this threshold is reached, the primary
# has already failed (3 retries x this many) times, not just this many.
FAILOVER_CONSECUTIVE_FAILURE_THRESHOLD = 2

# A candle/quote is considered "stale" (and treated as a failure for
# failover purposes) if its timestamp is older than this many minutes
# during market hours -- catches a broker returning HTTP 200 with
# genuinely stale data (no error to retry on) rather than an outright
# failure.
#
# WIDENED 10 -> 15 (live CI, 2026-07-28): a 15-min candle only 26
# minutes old (effective threshold was 10+15=25) tripped this and
# triggered an unnecessary Upstox -> Angel One failover mid-run --
# 1 minute past a threshold that tight is far more consistent with
# ordinary candle-publish/network lag than a genuine Upstox outage,
# and every spurious failover just shifts load onto Angel One, which
# is already the fragile side of this pair (see market_data.py's
# docstring). New effective threshold: 15+15=30 min for a 15-min
# candle -- still catches a real outage well within a run, with
# actual headroom over normal publish lag.
STALE_DATA_THRESHOLD_MIN = 15

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
# in the first place.
#
# CONFIRMED 2026-07-23 (was UNVERIFIED before -- see angelone.py's
# _throttle_historical_call() for the fix this enables): Angel One's
# own SmartAPI forum (smartapi.angelone.in/smartapi/forum, RateLimit
# docs + admin reply on "Changes in API Rate Limit") confirms
# getCandleData is subject to TWO separate caps, not one:
#   1. 1 request/second (this constant)
#   2. 180 requests/MINUTE -- "an additional cap on top of the
#      per-second ratelimit" (Angel One admin's own words) -- a
#      rolling-window cap the previous fixed-interval-only throttle
#      never checked, only guaranteed <=60/min as a side effect of the
#      1s spacing, with no headroom and no defense against other
#      Angel One API calls (LTP, option chain, expiry) sharing the
#      same account-wide bucket.
# A SEPARATE Angel One forum thread ("API Rate Limit checks are not
# perfect") reports clients running at 3-4x UNDER the documented limit
# still receiving spurious 403 "exceeding access rate" errors -- i.e.
# Angel One's own enforcement is inconsistent, not just this codebase
# under-throttling. ANGELONE_HISTORICAL_MAX_CALLS_PER_MINUTE below is
# set well under the documented 180 ceiling for that reason (same
# margin-for-safety approach OpenAlgo -- github.com/marketcalls/openalgo,
# an open-source Indian-broker trading framework -- takes: their own
# historical-data throttle commit caps at 120/min against a similarly
# generous documented ceiling elsewhere).
ANGELONE_HISTORICAL_MIN_INTERVAL_SECONDS = 1.0
ANGELONE_HISTORICAL_MAX_CALLS_PER_MINUTE = 100

# =============================================================================
# Early Breakout Engine (strategy/) -- additive, 2026-07-24.
# =============================================================================
# A SEPARATE signal path from the Trade Score Engine above (ENTRY_SCORE /
# TRADE_SCORE_WEIGHTS / PROBABILITY_BANDS etc.) -- none of those are changed
# by this section. See strategy/__init__.py's module docstring for why this
# engine exists (catching a move at Stage 1/2, not after it's already run).

# --- Stage 1: Base Formation (strategy/base_detector.py) ---
BASE_SCORE_MIN = 70              # floor to even consider a symbol+direction "basing"
BASE_LOOKBACK_CANDLES = 20        # 15-min candles used to judge the consolidation

# BUGFIX (2026-07-25, found while building app/market/snapshot.py): the
# engine used to fetch only BASE_LOOKBACK_CANDLES + 12 = 32 underlying
# candles, then compute EMA50 from them for breakout_score.trend_strength()
# (the "Underlying Trend" factor -- weight 20, the SINGLE LARGEST factor
# in EARLY_BREAKOUT_WEIGHTS). indicators.compute_ema() returns None
# whenever there are fewer closes than the period -- so ema50 was
# ALWAYS None with only 32 candles, meaning trend_strength() ALWAYS
# returned 0.0 (its own None-guard), silently, for every single
# candidate this engine has ever scored. Confirmed directly:
# compute_ema(list(range(1,33)), 50) -> None. Fetching enough candles
# for a genuine EMA50 fixes this; 60 leaves comfortable headroom above
# the bare minimum of 50.
EARLY_BREAKOUT_CANDLE_FETCH_COUNT = 60
BASE_HIGHER_LOWS_MIN = 3          # bullish base structure requirement
BASE_LOWER_HIGHS_MIN = 3          # bearish base structure requirement

# --- Stage 2/3: Breakout + Follow-Through (strategy/breakout_detector.py) ---
BREAKOUT_VOLUME_MULT = 1.5              # breakout candle volume vs. its own base-window average
FOLLOW_THROUGH_MIN_VOLUME_PCT = 0.8     # confirmation candle volume vs. the breakout candle's

# --- Stage 4: Extended Move rejection filter (strategy/breakout_detector.py) ---
# EXTENDED_MOVE_MAX_PREMIUM_PCT tightened 35% -> 25% on 2026-07-24 (the
# Institutional upgrade spec explicitly asked for a tighter 25% ceiling
# than the original Early Breakout Engine's 35% -- since this is one
# engine now, not two, the tighter number wins).
EXTENDED_MOVE_MAX_PREMIUM_PCT = 0.25    # reject if premium already moved >25% since the breakout candle
EXTENDED_MOVE_MAX_ATR_MULT = 2.0        # reject if price is already >2 ATR from EMA20
EXTENDED_MOVE_MAX_MOMENTUM_CANDLES = 3  # reject if >3 candles wider than 1x ATR have already printed ("3 Momentum Candles already printed")
PARABOLIC_BODY_GROWTH_MULT = 1.4        # reject if each of the last 3 candle bodies is >=1.4x the one before (accelerating = parabolic)

# --- Risk/Reward floor (strategy/early_breakout_scanner.py) ---
EARLY_BREAKOUT_MIN_RR = 2.0             # minimum projected 1:2 reward:risk

# --- Momentum ceilings (strategy/momentum.py) -- same "clamp(x/ceiling, 0, 1)"
# convention as trade_score.py's TS_*_CEILING constants, kept separate since
# this engine's momentum inputs (ADX/ROC/RSI-slope/MACD-hist) are entirely
# different indicators from the main engine's.
MOMENTUM_ADX_CEILING = 30.0              # ADX >= 30 -> full trend-strength score
MOMENTUM_ROC_CEILING_PCT = 1.5           # 1.5% signed rate-of-change -> full score
MOMENTUM_RSI_SLOPE_CEILING = 15.0        # signed RSI move over the lookback -> full score
MOMENTUM_MACD_CEILING_PCT = 0.003        # signed MACD histogram, as % of price -> full score
MOMENTUM_BODY_CEILING_PCT = 0.7          # candle body as % of its own range -> full score
MOMENTUM_VWAP_CEILING_PCT = 0.004        # signed distance from VWAP, as % of VWAP -> full score
# Added 2026-07-24 (Institutional upgrade) -- EMA Slope and Volume
# Acceleration, on top of the original 6 momentum sub-factors above.
MOMENTUM_EMA_SLOPE_CEILING_PCT = 0.002   # signed EMA9 slope over 3 candles, as % of price -> full score
MOMENTUM_VOLUME_ACCEL_CEILING = 0.5      # signed (recent-vs-prior volume ratio - 1.0) -> full score at +50%

# --- Fix 11: composite score weights (strategy/breakout_score.py) ---
# REPLACED 2026-07-24 (Institutional upgrade): was a 7-factor model
# (trend25/base20/breakout20/volume15/rr10/momentum5/liquidity5). This
# is one engine now, upgraded in place -- not a second parallel model --
# so the old weights are gone, not kept alongside these. New factors
# (institutional, follow_through, smart_money) reuse strengths already
# being computed for other reasons (Stage 2 gate, Stage 5 confirmation
# quality, Stage 4's smart-money read), so nothing here required new
# broker calls to add.
#
# NOTE: the spec's own weight table (Underlying Trend 20, Institutional
# 15, Base 15, Breakout 15, Follow-through 10, Volume 10, Momentum 5,
# Liquidity 5, Risk Reward 5, Smart Money 5) sums to 105, not 100 as its
# own "Total 100" line claims. Rather than silently picking different
# numbers, these are the spec's own values proportionally rescaled by
# 100/105 and rounded to whole points (largest remainder adjusted by 1
# point on Underlying Trend to land exactly on 100) -- same relative
# priority order the spec asked for, corrected to actually sum right.
EARLY_BREAKOUT_WEIGHTS = {
    "underlying_trend": 18,   # spec: 20
    "institutional": 14,      # spec: 15
    "base": 14,               # spec: 15
    "breakout": 14,           # spec: 15
    "follow_through": 10,     # spec: 10 (unchanged by rescale)
    "volume": 10,             # spec: 10 (unchanged by rescale)
    "momentum": 5,            # spec: 5 (unchanged by rescale)
    "liquidity": 5,           # spec: 5 (unchanged by rescale)
    "risk_reward": 5,         # spec: 5 (unchanged by rescale)
    "smart_money": 5,         # spec: 5 (unchanged by rescale)
}
assert sum(EARLY_BREAKOUT_WEIGHTS.values()) == 100, "EARLY_BREAKOUT_WEIGHTS must sum to 100"

# --- Fix 9: OI is a bonus, never a rejection reason, for this engine ---
EARLY_BREAKOUT_OI_BONUS = {
    "LONG_BUILDUP": 5,
    "SHORT_COVERING": 3,
    "NEUTRAL": 0,
}

# --- Fair Value Gap (app/engines/early_breakout/fair_value_gap.py, added
# 2026-07-25 -- the one sub-engine piloted from the "Institutional
# Intelligence Engine" request; see engine.py's module docstring for why
# only this one was built). Same "confirmation bonus, never a rejection
# reason" pattern as EARLY_BREAKOUT_OI_BONUS above -- an unfilled FVG in
# the trade's direction adds confidence; its absence doesn't reject a
# setup that's already cleared every other stage. ---
FVG_MIN_SIZE_ATR_FRACTION = 0.15   # gaps smaller than this fraction of ATR are noise, not counted
FVG_CONFIRMATION_BONUS = 4         # added to the composite score when an unfilled FVG confirms

# --- Fix 12: alert thresholds ---
# <60 Reject, 60-69 Watchlist only, 70-79 Telegram Alert, 80-89 High
# Conviction, 90+ Institutional Grade. Walked the same way
# TRADE_SCORE_GRADE_THRESHOLDS is walked (highest-first, first match wins).
EARLY_BREAKOUT_GRADE_THRESHOLDS = [
    (90, "Institutional Grade"),
    (80, "High Conviction"),
    (70, "Telegram Alert"),
    (60, "Watchlist"),
]
EARLY_BREAKOUT_TELEGRAM_MIN_SCORE = 70   # below this -> tracked internally only, per Fix 12's "60-69 Watchlist only"

# =============================================================================
# Institutional upgrade (strategy/institutional_score.py, smart_money_score.py)
# -- 2026-07-24, in-place upgrade of the Early Breakout Engine above, not a
# third engine. See EARLY_BREAKOUT_CHANGES.md / this session's summary for
# why building a separate third parallel scanner was rejected.
# =============================================================================

# --- Stage 2: Institutional Accumulation gate (strategy/institutional_score.py) ---
INSTITUTIONAL_SCORE_MIN = 70   # continue only when institutional_score >= 70 AND base_score >= 70

# --- Early Entry Window (strategy/early_breakout_scanner.py) ---
# Preferred 202-214 / Max 220 off a 200 breakout in the spec's own example
# -> +1% to +7% preferred, +10% hard ceiling.
EARLY_ENTRY_PREFERRED_MIN_PCT = 0.01
EARLY_ENTRY_PREFERRED_MAX_PCT = 0.07
EARLY_ENTRY_MAX_PCT = 0.10     # premium above breakout_price * (1 + this) -> Reject Entry, never chase

# --- Fresh Breakout Score (strategy/breakout_detector.py) ---
# Candle-age decay applied as a MULTIPLIER on the composite score, keyed
# by how many candles have closed since the breakout candle itself (0 =
# the breakout candle's own close; Stage 5 mandates waiting for the next
# candle before alerting, so age 0 is never actually reachable AT alert
# time -- it's kept in the table for completeness/fidelity to the spec
# and for any future stage that logs a pre-confirmation "watching" event).
FRESH_BREAKOUT_SCORE = {0: 100, 1: 90, 2: 75, 3: 40}   # age (candles since breakout) -> score /100
FRESH_BREAKOUT_MAX_AGE = 3     # age > this (i.e. a 5th+ candle) -> reject outright, per "Fourth Candle: Reject"

# =============================================================================
# Part 6 -- Enterprise Decision Engine (app/engines/decision_engine/)
# -- final-authority engine, sits between the existing scoring engines
# (Market Snapshot, Early Breakout, Trade Score Engine, Options
# Intelligence) and the alert layer. Additive only: computed and
# attached to every trade dict for visibility/logging regardless of
# these settings; DECISION_ENGINE_FILTER_ENABLED (default False) is
# the only flag that lets it actually block an alert, same rollout
# pattern as OPTIONS_INTELLIGENCE_FILTER_ENABLED above.
# =============================================================================

DECISION_ENGINE_ENABLED = True           # master flag -- False fully restores pre-Part-6 behavior
DECISION_ENGINE_FILTER_ENABLED = False   # if True, a rejected Decision Object blocks the alert

# --- Approval floors (Trade Validation / Rejection Engines) ---
DECISION_ENGINE_MIN_CONFIDENCE = 50.0      # 0-100
DECISION_ENGINE_MIN_PROBABILITY = 50.0     # 0-100 (win probability %)
DECISION_ENGINE_MIN_TRADE_QUALITY = 50.0   # 0-100
DECISION_ENGINE_MIN_RR = 1.5               # Risk/Reward Engine floor, same spirit as EARLY_BREAKOUT_MIN_RR

# --- Confidence Engine: per-plugin weights, renormalized automatically
# over whichever plugins are available for a given trade (base.py /
# confidence.py). Missing keys fall back to each plugin's default_weight. ---
DECISION_ENGINE_PLUGIN_WEIGHTS = {
    "market_snapshot": 0.15,
    "early_breakout": 0.35,
    "trade_score_engine": 0.35,
    "options_intelligence": 0.20,
}

# --- Position Size Engine -- one of: "fixed_quantity", "capital_based",
# "risk_based", "percentage_based", "dynamic" (dynamic = min of
# capital_based and risk_based, i.e. the more conservative of the two). ---
DECISION_ENGINE_POSITION_SIZE_MODE = "dynamic"
DECISION_ENGINE_FIXED_QTY = 1
DECISION_ENGINE_CAPITAL = 100000.0             # notional trading capital, purely for sizing math
DECISION_ENGINE_RISK_PCT_OF_CAPITAL = 0.01     # 1% of capital risked per trade (risk_based/dynamic)
DECISION_ENGINE_CAPITAL_PCT_PER_TRADE = 0.10   # 10% of capital allocated per trade (capital_based/dynamic)
DECISION_ENGINE_LOT_SIZE_DEFAULT = 1           # rounded down to a multiple of this many units

# --- Alert Priority Engine thresholds -- walked highest-first, first
# match wins, same style as EARLY_BREAKOUT_GRADE_THRESHOLDS /
# TRADE_SCORE_GRADE_THRESHOLDS elsewhere in this file. ---
DECISION_ENGINE_PRIORITY_THRESHOLDS = [
    (85, "Very High"),
    (70, "High"),
    (55, "Medium"),
    (0, "Low"),
]

# =============================================================================
# Part 7 -- Replay, Learning & Analytics Engine
# (app/engines/replay_analytics/) -- offline-only engine: replays
# historical candles against past signals, scores scanner accuracy, and
# generates recommendations. NEVER places orders, NEVER runs inside the
# live scanner/tracker loop, and NEVER auto-changes a production
# threshold above (ENTRY_SCORE, DECISION_ENGINE_MIN_* etc.) -- it only
# reads state.py's historical daily state files and its own journal,
# and writes its own reports/recommendations. Master flag below fully
# disables the whole engine; every sub-report has its own flag too so
# a user can turn on just, say, daily analytics without CSV/JSON export.
# =============================================================================

REPLAY_ANALYTICS_ENABLED = True   # master flag -- False short-circuits engine.run_*() to a no-op

# --- Replay Engine: supported timeframes for candle-by-candle replay.
# Values are minutes; "D" means one daily candle (source data is
# resampled UP from 1-minute candles for every entry below -- never
# fetched separately per timeframe). ---
REPLAY_TIMEFRAMES = ["1min", "3min", "5min", "15min", "30min", "1hour", "1day"]
REPLAY_TIMEFRAME_MINUTES = {
    "1min": 1, "3min": 3, "5min": 5, "15min": 15, "30min": 30, "1hour": 60,
}  # "1day" handled separately (calendar-day resample, not a fixed minute bucket)
REPLAY_DEFAULT_TIMEFRAME = "1min"   # matches tracker.py's native candle granularity

# --- Trade Outcome Engine ---
REPLAY_BREAKEVEN_BAND_PCT = 0.002   # exit within +/-0.2% of entry premium -> BREAKEVEN, not WIN/LOSS

# --- Performance Analytics: no thresholds needed (pure arithmetic on
# whatever trades are passed in), but the recovery-factor floor below
# distinguishes "no drawdown yet" (None) from a genuine 0. ---
REPLAY_MIN_DRAWDOWN_FOR_RECOVERY_FACTOR = 0.01   # below this, recovery_factor is reported as None (avoid div-by-~0 noise)

# --- Missed Opportunity / False Signal Engines: a "rejected" candidate
# (never alerted -- score fell below ENTRY_SCORE) counts as a "good
# trade rejected" if replaying it forward would have hit T1+ before SL. ---
REPLAY_MISSED_OPPORTUNITY_MIN_MOVE_PCT = 0.15   # forward move needed (as %, of premium) to call a rejected setup "missed"
REPLAY_LATE_ENTRY_MAX_CANDLES = 2               # entry filled >2 candles after signal candle -> "late entry"
REPLAY_EARLY_EXIT_MIN_REMAINING_MOVE_PCT = 0.10 # if price kept moving >=10% further favorably after exit -> "early exit"

REPLAY_FALSE_BREAKOUT_REVERSAL_PCT = 0.05   # price reverses >=5% against the breakout direction within REPLAY_LATE_ENTRY_MAX_CANDLES*3 candles -> false breakout/breakdown
REPLAY_WEAK_MOMENTUM_MAX_CANDLE_RANGE_PCT = 0.15   # avg candle range post-entry below this -> "weak momentum entry"

# --- Learning Engine (statistical only -- NO machine learning). Groups
# closed trades by these dimensions and ranks group win-rate/expectancy;
# a group needs at least this many trades before it's surfaced, to
# avoid "best timeframe: 1 trade, 100% win rate" noise. ---
REPLAY_LEARNING_MIN_SAMPLE_SIZE = 5

# --- Scoring Calibration: buckets predicted confidence into these
# bands and compares each band's actual win rate against its midpoint;
# a gap beyond this triggers a "recommend adjusting" note. Never
# auto-applies a new threshold -- see module docstring. ---
REPLAY_CALIBRATION_BANDS = [
    (85, 100, "Very High"), (70, 85, "High"), (55, 70, "Medium"), (0, 55, "Low"),
]
REPLAY_CALIBRATION_GAP_THRESHOLD_PCT = 15.0   # |predicted midpoint - actual win rate| beyond this -> flag for review

# --- Scanner Health Engine: 0-100 composite of signal accuracy,
# decision accuracy, false-positive rate, false-negative rate. Weights
# sum to 100. ---
REPLAY_HEALTH_SCORE_WEIGHTS = {
    "signal_accuracy": 35, "decision_accuracy": 30,
    "false_positive_penalty": 20, "false_negative_penalty": 15,
}
REPLAY_HEALTH_SCORE_BANDS = [
    (80, "Excellent"), (65, "Good"), (50, "Fair"), (0, "Poor"),
]

# --- Daily/Weekly/Monthly Analytics + Trade Journal persistence.
# Reuses daily_cache.py's existing state-branch JSON mechanism (same
# as opportunity_score.py/instrument_master.py) -- no new storage. ---
REPLAY_JOURNAL_SUBDIR = "replay_analytics/journal"
REPLAY_DAILY_REPORT_SUBDIR = "replay_analytics/daily"
REPLAY_WEEKLY_REPORT_SUBDIR = "replay_analytics/weekly"
REPLAY_MONTHLY_REPORT_SUBDIR = "replay_analytics/monthly"

REPLAY_DAILY_ANALYTICS_ENABLED = True
REPLAY_WEEKLY_ANALYTICS_ENABLED = True
REPLAY_MONTHLY_ANALYTICS_ENABLED = True

# --- Reporting: every export format independently toggleable, all
# default off except the in-memory summary dict (which callers always
# get back from engine.py regardless of these -- these three flags
# only control whether a FILE is additionally written to disk). ---
REPLAY_REPORT_CSV_ENABLED = False
REPLAY_REPORT_JSON_ENABLED = False
REPLAY_REPORT_OUTPUT_DIR = "replay_reports"   # relative path, created on first write

# --- Performance: replay result caching (per symbol+direction+date
# range key) so re-running analytics over an unchanged historical
# window doesn't re-replay every candle again. In-process only, same
# posture as cache.py's TTLCache -- see replay_engine.py. ---
REPLAY_ENABLE_RESULT_CACHE = True

# =============================================================================
# Part 11 -- Quant Intelligence Engine (app/engines/quant_intelligence/)
# -- independent probability/signal-quality/regime/risk/pattern/learning/
# explainability engine, merged in as a plug-in of the existing Part 6
# Decision Engine (decision_engine/quant_adapter.py). Additive only,
# same rollout posture as every other engine in this file: computed
# and attached to trade["decision"]["quant_intelligence"] for
# visibility/logging regardless of these settings; QUANT_INTELLIGENCE_
# INFLUENCE_ENABLED (default False) is the only flag that lets it
# actually change trade["decision"]["approved"]/priority, same pattern
# as DECISION_ENGINE_FILTER_ENABLED / OPTIONS_INTELLIGENCE_FILTER_ENABLED
# above. If the engine is unavailable, disabled, or raises for any
# reason, decision_engine/quant_adapter.py degrades to
# {"enabled": False, ...} and the rest of the Decision Engine output is
# completely unaffected -- see that module's docstring.
#
# The engine's own internal tuning (adaptive probability weights,
# signal-quality grade thresholds, regime/risk/pattern thresholds,
# cache TTL, etc.) lives in its own
# app/engines/quant_intelligence/config/settings.yaml, per Part 11's
# original design -- NOT duplicated here. Only the integration-level
# toggles below (whether it runs at all, and whether it's allowed to
# influence the decision) belong in this file.
# =============================================================================

QUANT_INTELLIGENCE_ENABLED = True             # master flag -- False fully restores pre-Part-11 behavior
QUANT_INTELLIGENCE_INFLUENCE_ENABLED = False  # if True, a REJECTED Quant Intelligence read can flip an
                                               # otherwise-approved trade to rejected (see quant_adapter.py)
QUANT_INTELLIGENCE_MIN_PROBABILITY = 40.0     # 0-100 -- only enforced when the influence flag above is True

# =============================================================================
# Part 11.5 -- News Intelligence Engine (app/engines/news_intelligence/)
# -- independent collector/sentiment/impact engine, merged in as a plug-in
# of the existing Part 6 Decision Engine (decision_engine/news_adapter.py),
# invoked after Quant Intelligence. Additive only, same rollout posture as
# every other engine in this file: computed and attached to
# trade["decision"]["news_intelligence"] for visibility/logging regardless
# of these settings; NEWS_INTELLIGENCE_INFLUENCE_ENABLED (default False) is
# the only flag that lets it actually change trade["decision"]["approved"].
# If the engine is unavailable, disabled, or raises for any reason,
# decision_engine/news_adapter.py degrades to {"enabled": False, ...} and
# the rest of the Decision Object (including Quant Intelligence's own
# output) is completely unaffected -- see that module's docstring.
#
# The engine's own internal tuning (providers, dedup, cache TTL, sentiment
# thresholds, impact weights, etc.) lives in its own
# app/engines/news_intelligence/config/settings.yaml, per its original
# design -- NOT duplicated here. Only the integration-level toggles below
# (whether it runs at all, and whether it's allowed to influence the
# decision) belong in this file.
# =============================================================================

NEWS_INTELLIGENCE_ENABLED = True             # master flag -- False fully restores pre-Part-11.5 behavior
NEWS_INTELLIGENCE_INFLUENCE_ENABLED = False  # if True, a confidently unfavorable News Intelligence read
                                              # can flip an otherwise-approved trade to rejected (see news_adapter.py)
NEWS_INTELLIGENCE_MIN_SCORE = -20.0          # -100..100, direction-aligned floor -- only enforced when the
                                              # influence flag above is True
NEWS_INTELLIGENCE_COLLECTION_TIMEOUT_SECONDS = 25.0  # hard wall-clock budget for the one collect() cycle
                                              # main.py runs at startup -- see main.py's own comment for why
                                              # this is a defense-in-depth cap on top of each provider's own
                                              # settings.yaml timeout_seconds, not a replacement for it.
