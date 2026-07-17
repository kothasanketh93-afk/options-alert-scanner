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

NOTE ON THIS SPLIT: the framework pieces now live in their own
modules so this file stays focused on the detection flow itself:
  - indicators.py    EMA/ATR/VWAP/efficiency-ratio/regime/S-R level
  - option_chain.py  broker calls: instrument keys, expiry, ATM
                      strike, leg extraction, VWAP-on-futures check,
                      multi-timeframe confirmation, directional bias
  - filters.py       quality-gate factor checks + the gate itself
  - levels.py        dynamic ATR-based SL/target/entry-zone sizing
  - grading.py       quality score, grade, confidence, risk rating
  - ranking.py       shared sort key + cross-symbol ranking
This file (scanner.py) wires them together: current_session(),
moneyness_offsets(), score_strike(), detect_setups(), and
detect_all_setups() -- the top-level detection flow.

WHAT CHANGED FROM V1:
- No numeric score reaches Telegram. Internally a 0-100 quality score
  still exists (compute_grade() needs it), but the trade dict now
  carries "grade" ("Strong Entry"/"Entry") and "confidence_stars" instead.
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
  "entry_low": 120.0, "entry_high": 123.6,   # ATR-based width now, see dynamic_trade_levels()
  "stop_loss": 108.4, "target_1": 145.9, "target_2": 168.2,  # ATR-based now
  "grade": "Strong Entry",            # replaces setup_score in Telegram
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
from logging_setup import get_logger
from config import (
    SESSION_WINDOWS, STRIKE_STEP, STRIKE_RANGE, SYMBOLS,
    ENTRY_SCORE, T1_ATR_MULT,
)

from indicators import ema_trend_direction, compute_atr, market_regime, find_nearby_level
from option_chain import (
    UNDERLYING_INSTRUMENT_KEYS, current_expiry, _extract_leg,
    vwap_check, multi_timeframe_confirms, directional_bias, get_atm_strike,
    resolve_underlying_key,
)
from filters import (
    oi_is_building, liquidity_ok, delta_acceptable, expiry_theta_ok,
    quality_gate, reasons_from_factors,
)
from levels import dynamic_trade_levels, estimated_holding_minutes
from grading import example_quality_score, compute_grade, confidence_display, risk_rating
from ranking import _grade_rank_key, rank_all

_logger = get_logger("scanner")

IST = timezone(timedelta(hours=5, minutes=30))


def current_session(now: datetime = None):
    now = now or datetime.now(IST)
    hhmm = (now.hour, now.minute)
    for start, end in SESSION_WINDOWS:
        if start <= hhmm < end:
            return f"{start[0]:02d}:{start[1]:02d}-{end[0]:02d}:{end[1]:02d}"
    return None


def moneyness_offsets(direction: str) -> dict:
    """
    Returns {"ITM": [offsets...], "ATM": [0], "OTM": [offsets...]} --
    a LIST of strike offsets per bucket now (was a single int per
    bucket before STRIKE_RANGE existed), so STRIKE_RANGE > 1 can widen
    how many strikes get evaluated on each side without changing the
    three-bucket vocabulary anything downstream (state.py's
    suppression, Trade ID format, Telegram alert's "Strike: {moneyness}"
    field) depends on. STRIKE_RANGE=1 (the default) reproduces the
    exact old behavior: one ITM strike, ATM, one OTM strike.
    """
    n = STRIKE_RANGE
    if direction == "CE":
        return {"ITM": list(range(-1, -n - 1, -1)), "ATM": [0], "OTM": list(range(1, n + 1))}
    return {"ITM": list(range(1, n + 1)), "ATM": [0], "OTM": list(range(-1, -n - 1, -1))}


def score_strike(symbol, direction, moneyness, strike, spot_price, session, oi_history) -> dict:
    underlying_key = resolve_underlying_key(symbol)
    expiry = current_expiry(symbol)

    chain = market_data.get_broker().get_option_chain(underlying_key, expiry)
    leg = _extract_leg(chain, strike, direction)
    if leg is None:
        _logger.info(f"SKIP {symbol} {direction} {moneyness} strike={strike}: leg not found in option chain")
        return None  # this strike isn't listed / no data -- not a gate failure, just absent

    instrument_key = leg["instrument_key"]
    premium = leg["ltp"]
    oi = leg["oi"]
    volume = leg["volume"]
    delta = leg["delta"]
    bid, ask = leg["bid"], leg["ask"]

    # BUGFIX: delta can legitimately be None here -- both angelone.py
    # (Greeks endpoint had no data for this strike: illiquid, off-hours,
    # or the documented weekly/monthly caveat) and upstox.py (Greeks
    # missing from that side's response) leave it unset rather than
    # guessing. delta_acceptable()/expiry_theta_ok() below call
    # abs(delta) unconditionally for OTM strikes -- with delta=None
    # that raises TypeError and crashes the whole scan run instead of
    # just skipping this one strike. Must be checked before ANY use of
    # delta, not just before dynamic_trade_levels() (the old None-check
    # further below ran too late -- delta_ok/expiry_ok had already used
    # it by then).
    if delta is None:
        _logger.info(f"SKIP {symbol} {direction} {moneyness} strike={strike}: delta unavailable (Greeks missing)")
        return None  # Greeks unavailable for this strike -- absent, not a gate failure

    # Same 15-min candles used by directional_bias()'s confirmation leg
    # and market_regime() -- reused here for find_nearby_level() so the
    # S/R cap is computed against the same trend read as everything else.
    candles_15m = market_data.get_recent_underlying_candles(underlying_key, "15minute", 30)
    if not candles_15m:
        _logger.info(f"SKIP {symbol} {direction} {moneyness} strike={strike}: no 15-min candles returned")
        return None

    ema_aligned = ema_trend_direction(candles_15m) == direction
    vwap_confirmed = vwap_check(symbol, spot_price, direction)
    volume_confirmed = liquidity_ok(oi, volume, bid, ask, premium, symbol)  # see NOTE below
    oi_building = oi_is_building(oi_history, instrument_key, oi)
    liquidity_pass = liquidity_ok(oi, volume, bid, ask, premium, symbol)
    mtf_confirmed = multi_timeframe_confirms(symbol, direction)
    delta_ok = delta_acceptable(moneyness, delta)
    expiry_ok = expiry_theta_ok(moneyness, delta, expiry)

    nearby_level = find_nearby_level(candles_15m, direction)
    atr_index_points = compute_atr(candles_15m)
    if atr_index_points is None or delta == 0:
        _logger.info(f"SKIP {symbol} {direction} {moneyness} strike={strike}: ATR unavailable or delta==0")
        return None  # dynamic_trade_levels() would raise on this -- bail before it does

    # Provisional T1 distance (pre-cap) just to test room against the
    # nearby level -- dynamic_trade_levels() below recomputes/caps this
    # properly; this is only for the risk_reward_ok gate input.
    provisional_t1_distance = atr_index_points * abs(delta) * T1_ATR_MULT
    if nearby_level is not None:
        index_distance_to_level = abs(nearby_level - spot_price)
        risk_reward_ok = (index_distance_to_level * abs(delta)) > provisional_t1_distance
    else:
        risk_reward_ok = True  # no level in the lookback window -- don't gate on absence of data

    factors = {
        "direction": direction,
        "ema_aligned": ema_aligned,
        "vwap_confirmed": vwap_confirmed,
        "volume_confirmed": volume_confirmed,
        "oi_building": oi_building,
        "liquidity_ok": liquidity_pass,
        "mtf_confirmed": mtf_confirmed,
        "risk_reward_ok": risk_reward_ok,
        "delta_ok": delta_ok,
        "expiry_theta_ok": expiry_ok,
    }

    # Full visibility into EVERY strike that reaches this point --
    # BUGFIX 2026-07-17: this used to log only AFTER the
    # `quality_gate()` check below, so a run where every single strike
    # failed the hard gate (liquidity/delta/theta -- the common case,
    # not an edge case) produced ZERO log lines, indistinguishable
    # from the scan not running at all. Logging here, before the
    # gate, means a fully-quiet day still shows exactly which hard
    # gate(s) failed on which strikes.
    hard_gate_pass = quality_gate(factors)
    factor_summary = " ".join(f"{k}={'Y' if v else 'N'}" for k, v in factors.items() if k != "direction")
    _logger.info(
        f"GATE {symbol} {direction} {moneyness} strike={strike} "
        f"hard_gate={'PASS' if hard_gate_pass else 'FAIL'} | {factor_summary}"
    )
    if not hard_gate_pass:
        return None

    # NOTE: volume_confirmed above reuses liquidity_ok()'s pass/fail as a
    # placeholder -- liquidity_ok() checks OI/volume/premium/spread
    # jointly, not "is volume expanding vs. its recent average" (that
    # needs a rolling average this function doesn't have inputs for).
    # If your existing scanner tracks recent average option volume,
    # swap this for a real expansion check; example_quality_score()'s
    # "volume_strength" docstring shows the intended shape.

    strengths = {
        "ema_strength": 1.0 if ema_aligned else 0.0,
        "mtf_agreement": 1.0 if mtf_confirmed else 0.0,
        "volume_strength": 1.0 if volume_confirmed else 0.0,
        "oi_build_strength": 1.0 if oi_building else 0.0,
        "liquidity_strength": 1.0 if liquidity_pass else 0.0,
        "risk_reward_strength": 1.0 if risk_reward_ok else 0.0,
    }
    quality_score = example_quality_score(strengths)
    grade = compute_grade(quality_score)

    _logger.info(
        f"SCORE {symbol} {direction} {moneyness} strike={strike} "
        f"score={quality_score}/100 grade={grade} (entry_floor={ENTRY_SCORE})"
    )

    if grade == "Reject":
        return None

    levels = dynamic_trade_levels(premium, atr_index_points, delta, direction, spot_price, nearby_level)
    stars, confidence_label = confidence_display(grade)
    atr_pct = (atr_index_points / spot_price) if spot_price else 0.0
    risk_emoji, risk_label = risk_rating(atr_pct, moneyness)
    target_distance = levels["target_1"] - premium
    holding_minutes = estimated_holding_minutes(atr_index_points, target_distance / abs(delta) if delta else 0)

    return {
        "symbol": symbol,
        "direction": direction,
        "moneyness": moneyness,
        "instrument_key": instrument_key,
        "strike": strike,
        "expiry": expiry,
        "spot_price": spot_price,
        "alert_premium": premium,
        "entry_low": levels["entry_low"],
        "entry_high": levels["entry_high"],
        "stop_loss": levels["stop_loss"],
        "target_1": levels["target_1"],
        "target_2": levels["target_2"],
        "grade": grade,
        "confidence_stars": stars,
        "confidence_label": confidence_label,
        "risk_rating": risk_emoji,
        "risk_label": risk_label,
        "expected_holding_minutes": holding_minutes,
        "why_reasons": reasons_from_factors(factors),
        "session": session,
        "alternatives": [],  # filled in by main.py's run_scanner_step, not here
        "_quality_score": quality_score,
    }


def detect_setups(symbol: str, oi_history: dict) -> list:
    """
    Returns ALL candidates for this symbol that passed the quality gate
    (across both CE/PE directions and ITM/ATM/OTM) -- NOT pre-reduced
    to a single winner. detect_all_setups() does a single flat ranking
    pass across every symbol's candidates together, so a symbol's own
    non-winning strikes still surface correctly as alternatives even
    when that symbol doesn't win overall.

    With STRIKE_RANGE > 1, each of the ITM/OTM buckets may span several
    strikes (e.g. ATM-2 and ATM-1 both "ITM" for a CE at STRIKE_RANGE=2)
    -- every strike in a bucket is scored, and only the single
    best-scoring strike from that bucket is kept, so the rest of the
    pipeline (suppression, Trade IDs, alert format) still ever sees
    exactly one ITM / one ATM / one OTM candidate per direction, same
    as when STRIKE_RANGE was always 1.
    """
    session = current_session()
    if session is None:
        return []  # outside a scan window -- deliberately silent, this is expected most of the day

    underlying_key = resolve_underlying_key(symbol)
    min_quality_score = ENTRY_SCORE  # floor matching the lowest grade, "Entry"
    if underlying_key:
        candles_15m = market_data.get_recent_underlying_candles(underlying_key, "15minute", 30)
        regime, penalty = market_regime(candles_15m)
        min_quality_score += penalty

    biases = directional_bias(symbol)
    if not biases:
        _logger.info(f"BIAS {symbol}: no directional bias (5-min/15-min EMA disagree or insufficient data) -- no strikes scored this run")
        return []

    candidates = []
    for direction in biases:
        atm_strike, spot_price = get_atm_strike(symbol)
        step = STRIKE_STEP[symbol]
        buckets = moneyness_offsets(direction)

        for moneyness, offset_list in buckets.items():
            bucket_setups = []
            for offset_steps in offset_list:
                strike = atm_strike + offset_steps * step
                setup = score_strike(symbol, direction, moneyness, strike, spot_price, session, oi_history)
                if setup and setup.get("_quality_score", 0) >= min_quality_score:
                    bucket_setups.append(setup)

            if bucket_setups:
                candidates.append(max(bucket_setups, key=_grade_rank_key))

    return candidates


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
