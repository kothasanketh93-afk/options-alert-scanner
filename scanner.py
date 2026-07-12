"""
scanner.py -- V2.0 Professional Options Decision Engine
---------------------------------------------------------
Rewritten per the V2.0 spec: quality over quantity. Target 2-4
high-grade trades/day, not a dozen borderline ones.

This is still the ONE file you plug your proprietary detection logic
into (EMA/breakout/volume reads, delta/OI/volume pulls per strike).
Everything else here -- the quality gate, grading, dynamic SL/target,
VWAP filter, regime detection, liquidity engine, strike ranking, and
cross-symbol opportunity ranking -- is the V2.0 framework around it.

WHAT CHANGED FROM V1:
- No numeric score reaches Telegram. Internally a 0-100 quality score
  still exists (compute_grade() needs it), but the trade dict now
  carries "grade" ("A+"/"A"/"B+"/"B") and "confidence_stars" instead.
- Fixed % SL/target replaced with ATR-based dynamic levels (see
  dynamic_trade_levels()), capped against nearby support/resistance.
- Trade Quality Gate: ALL of {EMA trend, VWAP, volume, OI, liquidity,
  15-min confirmation, risk/reward} must pass or the setup is
  rejected outright -- no partial-credit alert.
- ATM/ITM/OTM are still evaluated internally, but only the single
  BEST strike per symbol+direction ultimately becomes a trade.
  detect_all_setups() returns the full ranked list (rank_all()); the
  caller (main.py) picks the first candidate that isn't already open
  on that symbol+direction, falling through past a suppressed top
  pick rather than alerting nothing. Suppression is therefore per
  symbol+direction only now, not per-moneyness (state.py already
  updated to match).
- Across symbols (NIFTY/BANKNIFTY today; SENSEX/MIDCPNIFTY are NOT
  wired in yet -- explicitly deferred, add them the same way when
  ready), only the single best-ranked opportunity is sent as an
  active trade per run; others that also passed the gate are listed
  as text-only alternatives in the same message, not opened as trades.
- Circuit breaker tightened to 2 consecutive SL (state.py).

DELIBERATELY NOT USED (per spec): RSI, MACD, Bollinger Bands,
SuperTrend, Stochastic, Ichimoku, CCI, extra MA crossovers, ML/AI of
any kind. Every function below is plain deterministic arithmetic.

Required output schema per accepted setup (unchanged surface, new
fields added -- state.py/tracker.py/telegram_notify.py already handle
these):
{
  "symbol": "NIFTY" | "BANKNIFTY",
  "direction": "CE" | "PE",
  "moneyness": "ITM" | "ATM" | "OTM",     # winner among ranked candidates
  "instrument_key": "...",
  "strike": 24500,
  "expiry": "2026-07-17",
  "spot_price": 24487.3,
  "alert_premium": 121.2,
  "entry_low": 120.0, "entry_high": 123.6,   # unchanged, % based
  "stop_loss": 108.4, "target_1": 145.9, "target_2": 168.2,  # ATR-based now
  "grade": "A+",                     # replaces setup_score in Telegram
  "confidence_stars": "★★★★★",
  "confidence_label": "Very High",
  "risk_rating": "🟡",
  "risk_label": "Medium",
  "expected_holding_minutes": (20, 40),
  "why_reasons": ["EMA aligned", "Above VWAP", "Strong OI Build-up", ...],
  "session": "10:35-11:35",
  "alternatives": [ {symbol, direction, moneyness, grade}, ... ],  # text-only
}
"""

from datetime import datetime, timezone, timedelta

import market_data
from config import (
    SESSION_WINDOWS, STRIKE_STEP, MONEYNESS_DEPTH, SYMBOLS,
    EFFICIENCY_TRENDING_THRESHOLD, ATR_VOLATILE_PCT_THRESHOLD, REGIME_SCORE_PENALTY,
    GRADE_THRESHOLDS, CONFIDENCE_DISPLAY,
    MIN_OI, MIN_OPTION_VOLUME, MAX_SPREAD_PCT,
    SL_ATR_MULT, T1_ATR_MULT, T2_ATR_MULT,
    ENTRY_ZONE_ATR_FRACTION, MIN_ENTRY_ZONE_PCT, MAX_ENTRY_ZONE_PCT,
    OI_BUILD_THRESHOLD_PCT, MIN_DELTA_OTM,
    EXPIRY_DAY_MIN_OTM_DELTA, EXPIRY_DAY_THRESHOLD_DAYS,
)

IST = timezone(timedelta(hours=5, minutes=30))

# Environment/account-specific instrument identifiers -- NOT thresholds,
# so these stay here next to the code that uses them rather than in
# config.py (see config.py's docstring for the reasoning).
UNDERLYING_INSTRUMENT_KEYS = {
    "NIFTY": None,      # e.g. "NSE_INDEX|Nifty 50"  -- TODO fill in
    "BANKNIFTY": None,  # e.g. "NSE_INDEX|Nifty Bank" -- TODO fill in
}

# IMPORTANT: separate from UNDERLYING_INSTRUMENT_KEYS above on purpose.
# The raw index (NSE_INDEX|...) has no genuine traded volume -- it's a
# calculated value, not something bought/sold -- so every candle's
# volume reads 0 and compute_vwap() would divide 0/0 and return None
# forever, silently failing every VWAP check even after volume is
# correctly plumbed through market_data.py. VWAP needs an instrument
# that's ACTUALLY traded: the current-month futures contract. TODO:
# fill in with the near-month NIFTY/BANKNIFTY futures instrument key
# (these roll monthly -- you'll need to update them each expiry, or
# better, resolve the current-month key programmatically from Upstox's
# instrument master each run instead of hardcoding).
VWAP_INSTRUMENT_KEYS = {
    "NIFTY": None,      # e.g. current-month "NSE_FO|<NIFTY futures key>" -- TODO, rolls monthly
    "BANKNIFTY": None,  # e.g. current-month "NSE_FO|<BANKNIFTY futures key>" -- TODO, rolls monthly
}


def current_session(now: datetime = None):
    now = now or datetime.now(IST)
    hhmm = (now.hour, now.minute)
    for start, end in SESSION_WINDOWS:
        if start <= hhmm < end:
            return f"{start[0]:02d}:{start[1]:02d}-{end[0]:02d}:{end[1]:02d}"
    return None


def moneyness_offsets(direction: str) -> dict:
    d = MONEYNESS_DEPTH
    if direction == "CE":
        return {"ITM": -d, "ATM": 0, "OTM": +d}
    return {"ITM": +d, "ATM": 0, "OTM": -d}


# ---------------------------------------------------------------------
# Pure-math indicator helpers -- deterministic, no AI/ML.
# ---------------------------------------------------------------------

def compute_ema(closes: list, period: int):
    if len(closes) < period:
        return None
    k = 2 / (period + 1)
    ema = sum(closes[:period]) / period
    for price in closes[period:]:
        ema = price * k + ema * (1 - k)
    return ema


def ema_trend_direction(candles: list) -> str:
    closes = [c["close"] for c in candles]
    ema9 = compute_ema(closes, 9)
    ema21 = compute_ema(closes, 21)
    if ema9 is None or ema21 is None:
        return None
    if ema9 > ema21:
        return "CE"
    if ema9 < ema21:
        return "PE"
    return None


def compute_atr(candles: list, period: int = 14):
    if len(candles) < period + 1:
        return None
    true_ranges = []
    for i in range(1, len(candles)):
        h, l = candles[i]["high"], candles[i]["low"]
        prev_close = candles[i - 1]["close"]
        tr = max(h - l, abs(h - prev_close), abs(l - prev_close))
        true_ranges.append(tr)
    recent = true_ranges[-period:]
    return sum(recent) / len(recent)


def compute_vwap(candles: list):
    total_pv = 0.0
    total_v = 0.0
    for c in candles:
        vol = c.get("volume")
        if vol is None:
            return None
        typical_price = (c["high"] + c["low"] + c["close"]) / 3
        total_pv += typical_price * vol
        total_v += vol
    if total_v == 0:
        return None
    return total_pv / total_v


def vwap_confirms(spot_price: float, vwap, direction: str) -> bool:
    """Mandatory VWAP filter: CE requires price > VWAP, PE requires price < VWAP."""
    if vwap is None:
        return False  # no data -> fail closed, don't guess
    if direction == "CE":
        return spot_price > vwap
    return spot_price < vwap


def vwap_check(symbol: str, spot_price: float, direction: str) -> bool:
    """
    Fetches VWAP from the current-month FUTURES contract (not the
    index -- see VWAP_INSTRUMENT_KEYS comment above for why) and
    checks it against `direction`. Call this from score_strike()
    instead of wiring compute_vwap()/vwap_confirms() together
    yourself, so the "use futures, not index" requirement can't be
    silently skipped.

    Raises NotImplementedError (loud, not a silent False) if
    VWAP_INSTRUMENT_KEYS isn't configured for this symbol yet --
    better to know immediately than to have every trade quietly
    rejected with no explanation.
    """
    futures_key = VWAP_INSTRUMENT_KEYS.get(symbol)
    if not futures_key:
        raise NotImplementedError(
            f"VWAP_INSTRUMENT_KEYS['{symbol}'] not set -- fill in the "
            f"current-month futures instrument key before calling vwap_check()."
        )
    candles = market_data.get_recent_underlying_candles(futures_key, "5minute", 75)
    vwap = compute_vwap(candles)
    return vwap_confirms(spot_price, vwap, direction)


def efficiency_ratio(candles: list) -> float:
    closes = [c["close"] for c in candles]
    if len(closes) < 2:
        return 0.0
    net_move = abs(closes[-1] - closes[0])
    total_move = sum(abs(closes[i] - closes[i - 1]) for i in range(1, len(closes)))
    if total_move == 0:
        return 0.0
    return net_move / total_move


def market_regime(candles_15m: list) -> tuple:
    er = efficiency_ratio(candles_15m)
    atr = compute_atr(candles_15m)
    last_close = candles_15m[-1]["close"] if candles_15m else None

    if er >= EFFICIENCY_TRENDING_THRESHOLD:
        return "TRENDING", REGIME_SCORE_PENALTY["TRENDING"]

    if atr is not None and last_close:
        atr_pct = atr / last_close
        if atr_pct >= ATR_VOLATILE_PCT_THRESHOLD:
            return "VOLATILE", REGIME_SCORE_PENALTY["VOLATILE"]

    return "SIDEWAYS", REGIME_SCORE_PENALTY["SIDEWAYS"]


def multi_timeframe_confirms(symbol: str, candidate_direction: str) -> bool:
    underlying_key = UNDERLYING_INSTRUMENT_KEYS.get(symbol)
    if not underlying_key:
        raise NotImplementedError(f"UNDERLYING_INSTRUMENT_KEYS['{symbol}'] not set.")
    candles_15m = market_data.get_recent_underlying_candles(underlying_key, "15minute", 30)
    return ema_trend_direction(candles_15m) == candidate_direction


def oi_is_building(oi_history: dict, instrument_key: str, current_oi: float) -> bool:
    previous = oi_history.get(instrument_key)
    oi_history[instrument_key] = current_oi
    if previous is None or previous <= 0:
        return False
    return (current_oi - previous) / previous >= OI_BUILD_THRESHOLD_PCT


def liquidity_ok(oi: float, volume: float, bid: float, ask: float, premium: float) -> bool:
    if oi < MIN_OI:
        return False
    if volume < MIN_OPTION_VOLUME:
        return False
    if premium <= 0:
        return False
    spread_pct = (ask - bid) / premium
    if spread_pct > MAX_SPREAD_PCT:
        return False
    return True


def days_to_expiry(expiry_date_str: str, now: datetime = None) -> int:
    """expiry_date_str: 'YYYY-MM-DD'. Returns 0 on expiry day itself."""
    now = now or datetime.now(IST)
    expiry_date = datetime.strptime(expiry_date_str, "%Y-%m-%d").date()
    return (expiry_date - now.date()).days


def delta_acceptable(moneyness: str, delta: float) -> bool:
    """
    Rejects deep-OTM "lottery ticket" strikes: below MIN_DELTA_OTM,
    an OTM option's probability of reaching target is low enough that
    passing every other gate doesn't compensate for it. ATM/ITM aren't
    subject to this floor -- their delta is naturally higher.
    """
    if moneyness != "OTM":
        return True
    return abs(delta) >= MIN_DELTA_OTM


def expiry_theta_ok(moneyness: str, delta: float, expiry_date_str: str, now: datetime = None) -> bool:
    """
    Stricter delta floor specifically for OTM strikes within
    EXPIRY_DAY_THRESHOLD_DAYS of expiry, where theta decay accelerates
    sharply and dominates any directional signal. Separate from
    delta_acceptable()/MIN_DELTA_OTM because the risk compounds
    specifically in the last day(s) before expiry -- a delta that's
    fine three days out can be a bad trade the day before expiry.
    """
    if moneyness != "OTM":
        return True
    if days_to_expiry(expiry_date_str, now) > EXPIRY_DAY_THRESHOLD_DAYS:
        return True
    return abs(delta) >= EXPIRY_DAY_MIN_OTM_DELTA


def find_nearby_level(candles: list, direction: str, lookback: int = 20):
    window = candles[-lookback:]
    if not window:
        return None
    if direction == "CE":
        return max(c["high"] for c in window)
    return min(c["low"] for c in window)


def dynamic_trade_levels(premium: float, atr_index_points: float, delta: float,
                          direction: str, spot_price: float, nearby_level) -> dict:
    """
    Raises ValueError if atr_index_points or delta is non-positive --
    this represents bad/missing input data, and silently proceeding
    would produce SL/T1/T2 all equal to the entry premium (premium_atr
    would be 0), which the tracker would then read as an instant false
    SL or T1 hit on the very next candle. Fail loudly here instead so
    a bad data pull surfaces immediately rather than as a corrupted
    trade downstream.
    """
    if atr_index_points is None or atr_index_points <= 0:
        raise ValueError(f"dynamic_trade_levels: atr_index_points must be > 0, got {atr_index_points}")
    if delta is None or delta == 0:
        raise ValueError(f"dynamic_trade_levels: delta must be non-zero, got {delta}")

    delta = abs(delta)
    premium_atr = atr_index_points * delta

    sl = premium - premium_atr * SL_ATR_MULT
    t1 = premium + premium_atr * T1_ATR_MULT
    t2 = premium + premium_atr * T2_ATR_MULT

    if nearby_level is not None:
        index_distance_to_level = abs(nearby_level - spot_price)
        premium_distance_to_level = index_distance_to_level * delta
        capped_t2 = premium + premium_distance_to_level * 0.9
        t2 = min(t2, capped_t2)
        t1 = min(t1, t2 * 0.7 + premium * 0.3)

    sl = max(sl, premium * 0.5)  # floor: never risk more than 50% of premium

    # Entry zone now scales with volatility too, not a fixed 0.99x/1.02x --
    # floored/capped so it stays fillable on quiet days and sane on wild ones.
    entry_zone_width = premium_atr * ENTRY_ZONE_ATR_FRACTION
    entry_zone_pct = entry_zone_width / premium if premium > 0 else MIN_ENTRY_ZONE_PCT
    entry_zone_pct = max(MIN_ENTRY_ZONE_PCT, min(MAX_ENTRY_ZONE_PCT, entry_zone_pct))
    entry_low = premium * (1 - entry_zone_pct / 2)
    entry_high = premium * (1 + entry_zone_pct / 2)

    return {
        "entry_low": round(entry_low, 2),
        "entry_high": round(entry_high, 2),
        "stop_loss": round(sl, 2),
        "target_1": round(t1, 2),
        "target_2": round(t2, 2),
    }


def estimated_holding_minutes(atr_index_points: float, target_distance_index_points: float) -> tuple:
    if atr_index_points <= 0:
        return (20, 40)
    atr_units = target_distance_index_points / atr_index_points
    low = max(10, round(atr_units * 10))
    high = max(low + 10, round(atr_units * 20))
    return (low, high)


def compute_grade(quality_score: int) -> str:
    for threshold, grade in GRADE_THRESHOLDS:
        if quality_score >= threshold:
            return grade
    return "Reject"


def confidence_display(grade: str) -> tuple:
    return CONFIDENCE_DISPLAY.get(grade, ("☆☆☆☆☆", "N/A"))


def risk_rating(atr_pct: float, moneyness: str) -> tuple:
    base = 0
    if atr_pct >= ATR_VOLATILE_PCT_THRESHOLD:
        base += 2
    elif atr_pct >= ATR_VOLATILE_PCT_THRESHOLD * 0.5:
        base += 1

    if moneyness == "OTM":
        base += 1
    elif moneyness == "ITM":
        base -= 1

    if base <= 0:
        return "🟢", "Low"
    if base == 1:
        return "🟡", "Medium"
    return "🔴", "High"


# ---------------------------------------------------------------------
# Trade Quality Gate + strike ranking + cross-symbol opportunity ranking
# ---------------------------------------------------------------------

def quality_gate(factors: dict) -> bool:
    required = (
        "ema_aligned", "vwap_confirmed", "volume_confirmed", "oi_building",
        "liquidity_ok", "mtf_confirmed", "risk_reward_ok",
        "delta_ok", "expiry_theta_ok",
    )
    return all(factors.get(k) is True for k in required)


def reasons_from_factors(factors: dict) -> list:
    labels = {
        "ema_aligned": "EMA aligned",
        "vwap_confirmed": "Above VWAP" if factors.get("direction") == "CE" else "Below VWAP",
        "volume_confirmed": "Volume Expansion",
        "oi_building": "Strong OI Build-up",
        "liquidity_ok": "Excellent Liquidity",
        "mtf_confirmed": "15-minute Confirmed",
        "risk_reward_ok": "No Nearby Resistance" if factors.get("direction") == "CE" else "No Nearby Support",
        "delta_ok": "Healthy Delta",
        "expiry_theta_ok": "Theta Risk Acceptable",
    }
    return [f"✓ {labels[k]}" for k in labels if factors.get(k) is True]


def rank_all(candidates: list) -> list:
    """
    Returns ALL candidates sorted best-to-worst (grade, then internal
    quality score). main.py needs
    the full ranked list so it can fall through to the next-best
    candidate if the top one is suppressed (already has an open trade
    on that symbol+direction) rather than silently alerting nothing.
    """
    grade_order = {"A+": 4, "A": 3, "B+": 2, "B": 1}
    return sorted(
        candidates,
        key=lambda s: (grade_order.get(s["grade"], 0), s.get("_quality_score", 0)),
        reverse=True,
    )


# ---------------------------------------------------------------------
# Main detection flow -- fill in the TODO functions with your existing
# EMA/breakout/volume logic, calling the helpers above from inside.
# ---------------------------------------------------------------------

def detect_setups(symbol: str, oi_history: dict) -> list:
    """
    Returns ALL candidates for this symbol that passed the quality gate
    (across both CE/PE directions and all moneyness levels) -- NOT
    pre-reduced to a single winner. detect_all_setups() does a single
    flat ranking pass across every symbol's candidates together, so a
    symbol's own non-winning strikes still surface correctly as
    alternatives even when that symbol doesn't win overall.
    """
    session = current_session()
    if session is None:
        return []

    underlying_key = UNDERLYING_INSTRUMENT_KEYS.get(symbol)
    min_quality_score = 60  # floor matching the lowest grade, "B"
    if underlying_key:
        candles_15m = market_data.get_recent_underlying_candles(underlying_key, "15minute", 30)
        regime, penalty = market_regime(candles_15m)
        min_quality_score += penalty

    candidates = []
    for direction in directional_bias(symbol):
        atm_strike, spot_price = get_atm_strike(symbol)
        step = STRIKE_STEP[symbol]
        offsets = moneyness_offsets(direction)

        for moneyness, offset_steps in offsets.items():
            strike = atm_strike + offset_steps * step
            setup = score_strike(symbol, direction, moneyness, strike, spot_price, session, oi_history)
            if setup and setup.get("_quality_score", 0) >= min_quality_score:
                candidates.append(setup)

    return candidates


def directional_bias(symbol: str) -> list:
    """
    TODO: return ["CE"], ["PE"], both, or [] based on your EMA/breakout/
    volume checks for `symbol`. Call multi_timeframe_confirms() to
    require 15-min agreement before including a direction.
    """
    return []  # stub -- no bias detected until you wire this in


def get_atm_strike(symbol: str):
    """
    TODO: fetch current spot price, round to the nearest strike step.
    Returns (atm_strike, spot_price).
    """
    raise NotImplementedError


def example_quality_score(strengths: dict) -> int:
    """
    REFERENCE implementation for the continuous 0-100 "_quality_score"
    that compute_grade() needs -- quality_gate() only tells you
    pass/fail, it can't differentiate an A+ from a B among setups that
    all passed. This turns each factor's STRENGTH (not just true/false)
    into a weighted composite. Adapt the weights/inputs to your own
    judgment -- this is a working starting point, not a mandate.

    Expected `strengths` dict, all values 0.0-1.0 (already normalized
    by the caller):
      ema_strength        -- how cleanly EMA9/21 are separated (e.g.
                              spread as % of price, capped at 1.0)
      mtf_agreement        -- 1.0 if multi_timeframe_confirms() was
                              True, 0.0 otherwise (binary factor
                              folded into the weighted score)
      volume_strength      -- current volume / recent average volume,
                              capped at 1.0 (e.g. 2x average = 1.0)
      oi_build_strength    -- OI growth % since last reading, scaled
                              against OI_BUILD_THRESHOLD_PCT (e.g. 2x
                              the minimum threshold = 1.0)
      liquidity_strength   -- how far OI/volume exceed the liquidity
                              minimums and how tight the spread is,
                              averaged, capped at 1.0
      risk_reward_strength -- (target distance / SL distance) scaled
                              against a baseline of 1.5, capped at 1.0

    Weights sum to 100 -- adjust these to match what you've found
    actually predicts good outcomes once you have a few weeks of
    daily-summary data to check against (see the confluence layer
    notes below for the manual weekly correlation check).
    """
    weights = {
        "ema_strength": 20,
        "mtf_agreement": 20,
        "volume_strength": 15,
        "oi_build_strength": 15,
        "liquidity_strength": 15,
        "risk_reward_strength": 15,
    }
    score = 0.0
    for key, weight in weights.items():
        value = max(0.0, min(1.0, strengths.get(key, 0.0)))  # clamp defensively
        score += value * weight
    return round(score)


def score_strike(symbol, direction, moneyness, strike, spot_price, session, oi_history) -> dict:
    """
    TODO: for this specific (symbol, direction, strike):
      1. Resolve instrument_key + expiry, pull premium/OI/volume/delta/
         bid/ask for THIS strike.
      2. Build the `factors` dict (ema_aligned, vwap_confirmed via
         vwap_check() -- NOT compute_vwap()/vwap_confirms() directly,
         since vwap_check() correctly uses the futures instrument
         rather than the volume-less index -- volume_confirmed,
         oi_building via oi_is_building(), liquidity_ok via
         liquidity_ok(), mtf_confirmed via multi_timeframe_confirms(),
         risk_reward_ok, delta_ok via delta_acceptable(), expiry_theta_ok
         via expiry_theta_ok()) and call quality_gate(factors) -- if
         False, return None immediately, no partial credit.
      3. If the gate passes, compute your internal 0-100 quality_score
         and map it with compute_grade(). If compute_grade() returns
         "Reject", return None.
      4. Call dynamic_trade_levels() for SL/target, confidence_display(),
         risk_rating(), estimated_holding_minutes(), reasons_from_factors().
      5. Return the setup dict per the module docstring schema. Stash
         the internal score as "_quality_score" (underscore = internal,
         telegram_notify.py must never read/display this field).

    Returns None until implemented.
    """
    return None


def detect_all_setups(oi_history: dict) -> list:
    """
    Gathers every candidate that passed the quality gate across ALL
    symbols/directions/moneyness into one flat list, ranked best-to-
    worst. Returns the FULL ranked list (not just the top one) -- the
    caller (main.py) is responsible for walking down it and picking
    the first candidate whose symbol+direction doesn't already have an
    open trade, falling through past any suppressed top candidate
    rather than alerting nothing for the whole run. The candidate that
    ends up chosen should have the rest of this list (minus itself)
    attached as its "alternatives" for the Telegram message.
    """
    all_candidates = []
    for symbol in SYMBOLS:
        all_candidates.extend(detect_setups(symbol, oi_history))

    return rank_all(all_candidates)
