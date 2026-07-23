"""
btst_config.py
---------------
Every BTST-specific tunable in one place, mirroring config.py's own
"nothing trading-relevant hardcoded elsewhere" convention -- kept as a
SEPARATE file (not added into config.py) so the overnight feature can
never accidentally change a threshold the intraday system also reads.

Nothing in this file is imported by config.py, scanner.py, tracker.py,
state.py, or main.py -- the intraday pipeline has zero knowledge this
file exists.
"""

# ---------------------------------------------------------------------
# Universe
# ---------------------------------------------------------------------
# Start narrow on purpose (see README discussion): NIFTY/BANKNIFTY plus
# only the most liquid names from the existing FALLBACK_STOCK_WATCHLIST,
# not the full ~100-stock universe. Widen this list only after a few
# weeks of live BTST data confirm the gap-handling is solid.
BTST_INDEX_SYMBOLS = ["NIFTY", "BANKNIFTY"]
BTST_STOCK_SYMBOLS = [
    "RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "TCS",
    "SBIN", "AXISBANK", "LT", "TATASTEEL", "SUNPHARMA",
]
BTST_SYMBOLS = BTST_INDEX_SYMBOLS + BTST_STOCK_SYMBOLS

# ---------------------------------------------------------------------
# Entry window (evening scan) -- run once, near the close, so there's
# still live liquidity/quotes but the day's trend is largely settled.
# ---------------------------------------------------------------------
BTST_ENTRY_WINDOW_HHMM = ((15, 0), (15, 20))  # 3:00-3:20 PM IST

# Re-use scanner.py's own ENTRY_SCORE-equivalent quality bar, but
# stricter -- overnight risk should only be taken on the best setups,
# not the same bar as a 20-minute intraday trade.
BTST_MIN_TRADE_SCORE = 70

# Never buy an option expiring the same day it would be entered or the
# very next day -- one full day of theta decay overnight already eats
# into an OTM buyer's edge; expiring same/next day makes it worse and
# risks the contract going worthless from theta alone regardless of
# direction. Applies to both indices and stocks.
BTST_MIN_DAYS_TO_EXPIRY = 2

# ---------------------------------------------------------------------
# Overnight gap-risk sizing -- percentage-of-premium based, NOT the
# intraday ATR-based dynamic_trade_levels() -- deliberately decoupled
# from levels.py's internals (see btst_levels.py) so this file can be
# tuned without touching, or needing to understand, the intraday ATR
# math at all.
#
# These are wider than intraday's effective ~1x ATR SL / entry-zone on
# purpose -- an 18-hour overnight window needs room for normal noise
# that would otherwise false-trigger a same-day-tight stop. NOT
# backtested -- same disclaimer config.py already carries for its own
# weights: tune from live SCORE/GATE-style logs once outcomes
# accumulate, see btst_scanner.py's logging.
# ---------------------------------------------------------------------
BTST_SL_PCT = 0.25       # stop loss = 25% below entry premium
BTST_T1_PCT = 0.20       # target 1 = 20% above entry premium
BTST_T2_PCT = 0.40       # target 2 = 40% above entry premium
BTST_ENTRY_ZONE_PCT = 0.02  # +/-2% around the EOD premium read

# ---------------------------------------------------------------------
# Exit-day handling (next trading day)
# ---------------------------------------------------------------------
# If, on the exit day's FIRST candle, price has already gapped past the
# stop loss, that's a genuine gap-down -- exit immediately at the first
# available price rather than waiting for a "clean" SL touch, since the
# configured stop_loss price itself is not fillable in a gap.
BTST_GAP_CHECK_ENABLED = True

# Force-exit time on the exit day if neither target nor SL has been
# hit by then -- BTST positions are not meant to roll a second night.
BTST_FORCE_EXIT_HHMM = (15, 15)

# How many past trading days' entry-date files run_exit_step() re-checks
# for still-open positions, not just "yesterday". Guards against a
# missed/failed exit-workflow run permanently orphaning open positions
# with real money in them -- see btst_main.run_exit_step()'s docstring.
BTST_LOOKBACK_TRADING_DAYS = 5

# ---------------------------------------------------------------------
# Stock-specific extra safety (see btst_earnings_guard.py)
# ---------------------------------------------------------------------
# Stricter than config.py's STOCK_MIN_OI/STOCK_MIN_OPTION_VOLUME --
# overnight-carry needs more room to exit cleanly the next morning
# than a same-day round-trip does.
BTST_STOCK_MIN_OI = 15_000
BTST_STOCK_MIN_OPTION_VOLUME = 2_000

# ---------------------------------------------------------------------
# Risk cap -- max BTST positions open at once, overnight. Deliberately
# its own explicit number, NOT derived from len(BTST_SYMBOLS) -- tying
# a risk cap to an unrelated list's length is exactly the kind of
# implicit coupling that silently breaks the day BTST_SYMBOLS is
# edited for an unrelated reason.
# ---------------------------------------------------------------------
BTST_MAX_CONCURRENT_POSITIONS = 5
