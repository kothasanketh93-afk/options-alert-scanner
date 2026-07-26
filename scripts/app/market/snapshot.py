"""
app/market/snapshot.py -- Market Intelligence snapshot, added 2026-07-25.
------------------------------------------------------------------------------
A scoped pilot slice of a much larger "Market Intelligence Engine" request
(16 sub-engines: Market Regime, Multi-Timeframe Analysis, Index
Intelligence, Market Breadth, Volatility, Volume Intelligence, Momentum,
Trend Confirmation, Support/Resistance, Market Structure, Liquidity, Gap
Analysis, Session Intelligence, Market Strength, Market Confidence, Market
Filter). Only the pieces below were built -- see "What's NOT here" below
for the rest and why.

WHY THIS SLICE: `app.engines.early_breakout.engine` and `scanner.py` (the
original Trade Score Engine) each independently fetch the same underlying
candles and recompute EMA9/21/50, ATR, and a basic regime read for the
SAME symbol, in the SAME scan cycle -- literally the "Repeated Indicator
Computations" the brief says to avoid. This module doesn't invent new
analysis; it consolidates indicators that already exist (indicators.py's
market_regime(), ema_trend_direction(), efficiency_ratio(), compute_atr(),
compute_vwap(); scanner.current_session()) into ONE object, computed once
per symbol per process (this is a once-per-run script, not a long-lived
server -- see the module-level cache note in get_market_snapshot() for why
that's enough here without a TTL or explicit invalidation), so a second
caller asking for the same symbol in the same run gets the cached result
instead of recomputing it.

WHAT'S NOT HERE (and why):
  - Market Breadth (Advance/Decline ratio, sector breadth): needs
    quotes for a broad market universe (NSE500-scale), not just the
    ~19 symbols this scanner already tracks. No existing data source
    for that here -- would need a new broker call pattern, out of
    scope for a bounded pilot.
  - Multi-Timeframe Analysis across 1m/3m/5m/15m/30m/1h/Daily: this
    scanner only ever fetches 5min/15min/daily today. Adding 1m/3m/
    30m/1h would be genuinely NEW broker API calls -- directly against
    this same session's earlier rate-limit fixes for Angel One. Only
    5min/15min/daily (already fetched elsewhere) are used here.
  - Liquidity Engine (bid-ask depth, order book): same constraint
    already documented in smart_money_score.py -- neither broker
    integration provides L2 depth data.
  - Index Intelligence's cross-index "Relative Strength", Market
    Confidence/Filter Engine as separate gates: would substantially
    overlap institutional_score.py / breakout_score.py's existing
    gating, which already reject on weak trend/volume/liquidity.
    Re-implementing the same gate under a new name isn't a genuine
    addition.

WIRED INTO: app.engines.early_breakout.engine only, in this pass --
NOT into scanner.py (the original, already-stable Trade Score Engine).
Wiring a shared object into a second, already-live engine is exactly the
kind of change that's safest done as its own separately-reviewed step,
not bundled into the same commit that introduces the object for the
first time.
"""

from datetime import datetime, timezone, timedelta

from indicators import (
    market_regime, ema_trend_direction, efficiency_ratio, compute_atr, compute_vwap, compute_ema,
)
from config import (
    TREND_STRENGTH_BANDS, VOLATILITY_SCORE_ATR_PCT_CEILING, VOLATILITY_LEVEL_BANDS,
    GAP_MIN_PCT, EARLY_BREAKOUT_CANDLE_FETCH_COUNT,
)

IST = timezone(timedelta(hours=5, minutes=30))

# Module-level, per-process cache -- this scanner runs as a fresh
# process per scan cycle (GitHub Actions cron invokes `python
# scripts/main.py` on a schedule, it isn't a long-lived server), so a
# plain dict that lives for the process's lifetime already gives
# "compute once per symbol per cycle" for free, with no TTL or
# explicit clear-between-cycles logic needed -- the next cycle is a
# new process with a fresh, empty cache.
_snapshot_cache = {}


def _trend_strength_label(er: float) -> str:
    """Bands efficiency_ratio() (0-1, already computed by
    indicators.market_regime() internally) into the five labels Part 2
    asked for. Reuses the SAME efficiency_ratio() reading market_regime()
    uses -- not a second, differently-tuned trend-strength calculation."""
    for threshold, label in TREND_STRENGTH_BANDS:
        if er >= threshold:
            return label
    return "Very Weak"


def _volatility_level(atr_pct: float) -> str:
    for threshold, label in VOLATILITY_LEVEL_BANDS:
        if atr_pct >= threshold:
            return label
    return "Low"


def _gap_info(daily_candles: list) -> dict:
    """
    Gap Up / Gap Down at today's session open vs. the PREVIOUS daily
    candle's close -- `daily_candles` here is yesterday-and-earlier
    only (market_data.get_recent_daily_candles()'s own docstring notes
    "today's daily candle doesn't exist pre-market"), so "today's open"
    has to come from the caller's own intraday candles, not from this
    daily series. See get_market_snapshot() for how the two are combined.
    """
    if len(daily_candles) < 1:
        return {"direction": None, "gap_pct": 0.0, "prev_close": None}
    prev_close = daily_candles[-1]["close"]
    return {"direction": None, "gap_pct": 0.0, "prev_close": prev_close}  # direction/gap_pct filled in by caller once today's open is known


def get_market_snapshot(symbol: str) -> dict:
    """
    Returns (and caches for the rest of this process's run):
      symbol, regime ("TRENDING_BULLISH"|"TRENDING_BEARISH"|"VOLATILE"|
      "SIDEWAYS"), trend_strength_label, volatility_score (0-100),
      volatility_level ("Low"|"Normal"|"High"), gap (dict from
      _gap_info() above, direction/gap_pct filled in), ema9/ema21/ema50/
      atr/vwap (so callers that already need these don't fetch+compute
      them a second time), timestamp.

    Returns None if there isn't enough candle data yet (e.g. right at
    market open) -- callers should treat that the same as "no bias
    available yet", not as an error.
    """
    if symbol in _snapshot_cache:
        return _snapshot_cache[symbol]

    # Deferred (not module-level) imports: market_data.py pulls in the
    # broker stack (angelone.py -> auth.py -> pyotp), which needs real
    # credentials/dependencies to even import. Keeping those out of this
    # module's top-level imports means the pure banding helpers above
    # (_trend_strength_label, _volatility_level) -- and this module
    # itself -- stay importable and testable without broker credentials,
    # same test-isolation reasoning as app.engines.early_breakout's
    # lazy __init__.py.
    import market_data
    from option_chain import resolve_underlying_key

    underlying_key = resolve_underlying_key(symbol)
    if not underlying_key:
        return None

    candles_15m = market_data.get_recent_underlying_candles(underlying_key, "15minute", EARLY_BREAKOUT_CANDLE_FETCH_COUNT)
    if len(candles_15m) < 22:
        return None

    closes = [c["close"] for c in candles_15m]
    er = efficiency_ratio(candles_15m)
    regime_label, _penalty = market_regime(candles_15m)
    direction = ema_trend_direction(candles_15m)

    if regime_label == "TRENDING" and direction is not None:
        regime = "TRENDING_BULLISH" if direction == "CE" else "TRENDING_BEARISH"
    else:
        regime = regime_label  # "SIDEWAYS" or "VOLATILE" -- no direction claimed for either

    atr = compute_atr(candles_15m)
    last_close = closes[-1]
    atr_pct = (atr / last_close) if (atr and last_close) else 0.0
    volatility_score = round(min(100.0, (atr_pct / VOLATILITY_SCORE_ATR_PCT_CEILING) * 100))

    vwap = compute_vwap(candles_15m[-20:])

    daily_candles = market_data.get_recent_daily_candles(underlying_key, 5)
    gap = _gap_info(daily_candles)
    todays_open = candles_15m[0]["open"] if candles_15m else None
    if gap["prev_close"] and todays_open:
        gap_pct = (todays_open - gap["prev_close"]) / gap["prev_close"]
        if abs(gap_pct) >= GAP_MIN_PCT:
            gap["direction"] = "UP" if gap_pct > 0 else "DOWN"
            gap["gap_pct"] = round(gap_pct, 4)

    # EMA9/20/21/50 aren't exposed in a directly-reusable form by
    # market_regime()/ema_trend_direction() -- compute once here so
    # engine.py (and any future caller) can read them from the
    # snapshot instead of computing their own copy from the same candles.
    ema9 = compute_ema(closes, 9)
    ema20 = compute_ema(closes, 20)
    ema21 = compute_ema(closes, 21)
    ema50 = compute_ema(closes, 50)

    snapshot = {
        "symbol": symbol,
        "regime": regime,
        "trend_strength_label": _trend_strength_label(er),
        "volatility_score": volatility_score,
        "volatility_level": _volatility_level(atr_pct),
        "gap": gap,
        "ema9": ema9,
        "ema20": ema20,
        "ema21": ema21,
        "ema50": ema50,
        "atr": atr,
        "vwap": vwap,
        "timestamp": datetime.now(IST).isoformat(),
    }

    _snapshot_cache[symbol] = snapshot
    return snapshot
