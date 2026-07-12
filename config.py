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
MONEYNESS_DEPTH = 1  # how many strikes ITM/OTM to evaluate alongside ATM

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
GRADE_THRESHOLDS = [
    (90, "A+"),
    (80, "A"),
    (70, "B+"),
    (60, "B"),
]

CONFIDENCE_DISPLAY = {
    "A+": ("★★★★★", "Very High"),
    "A":  ("★★★★☆", "High"),
    "B+": ("★★★☆☆", "Moderate"),
    "B":  ("★★☆☆☆", "Fair"),
}

# --- Liquidity engine (scanner.py) ---
MIN_OI = 50_000             # TODO: tune against your actual liquidity data
MIN_OPTION_VOLUME = 10_000  # TODO: tune
MAX_SPREAD_PCT = 0.03       # bid-ask spread must be <= 3% of premium

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
STALE_THRESHOLD_MIN = 45  # 30-min heartbeat interval + 7-min trader cadence + buffer
MARKET_OPEN_HHMM = (9, 15)
MARKET_CLOSE_HHMM_HEARTBEAT = (15, 30)
STARTUP_GRACE_MIN = 15    # skip staleness check for this long after market open

# --- Rolling log (rolling_log.py) ---
ROLLING_LOG_DAYS = 30

# --- Network retry (market_data.py, telegram_notify.py) ---
# Retry only transient failures (connection errors, timeouts, rate
# limits, momentary 5xx) -- never auth errors or other 4xx, which need
# a human, not a retry loop.
RETRY_MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 1.5
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
