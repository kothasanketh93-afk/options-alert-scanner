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
STRIKE_STEP = {"NIFTY": 50, "BANKNIFTY": 100}

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

# SENSEX/MIDCPNIFTY are explicitly deferred -- see README "Explicitly
# deferred" section for the exchange/expiry-day differences that need
# handling before adding them here.
SYMBOLS = ["NIFTY", "BANKNIFTY"]

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
ENTRY_SCORE = 75
STRONG_ENTRY = 85

GRADE_THRESHOLDS = [
    (STRONG_ENTRY, "Strong Entry"),
    (ENTRY_SCORE, "Entry"),
]

CONFIDENCE_DISPLAY = {
    "Strong Entry": ("★★★★★", "Very High"),
    "Entry":        ("★★★☆☆", "Moderate"),
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
SUMMARY_HHMM = (15, 25)

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
