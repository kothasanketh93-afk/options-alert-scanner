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
  - indicators.py    EMA/ATR/VWAP/efficiency-ratio/regime/S-R level/momentum
  - option_chain.py  broker calls: instrument keys, expiry, ATM
                      strike, leg extraction, VWAP-on-futures check
                      (boolean + signed distance), multi-timeframe
                      confirmation, directional bias
  - filters.py       quality-gate factor checks + the gate itself
  - levels.py        dynamic ATR-based SL/target/entry-zone sizing
  - grading.py       OLD (V2.0) binary-strengths scoring -- unused by
                      this file since Phase 3, kept only as a
                      reference/fallback; nothing else imports it
  - trade_score.py   PHASE 3: continuous-strength Trade Score Engine
                      (Trade Score, Probability, Confidence, Risk/
                      Reward scores, Grade, Expected R:R) -- this is
                      what score_strike() actually calls now
  - ranking.py       shared sort key + cross-symbol ranking
This file (scanner.py) wires them together: current_session(),
moneyness_offsets(), score_strike(), detect_setups(), and
detect_all_setups() -- the top-level detection flow.

WHAT CHANGED FROM V1:
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
This also means Phase 3's Trade Score Engine (trade_score.py) does
NOT use RSI or PCR, even though the phase-3 spec assumed both were
already being calculated -- neither was, so trade_score.py's 10
factors are built entirely from what's already computed here instead
(see that module's docstring for the full explanation).

Required output schema per accepted setup (state.py/tracker.py/
telegram_notify.py already handle these):
{
  "symbol": "NIFTY" | "BANKNIFTY" | ... (100-stock universe, Phase 1),
  "direction": "CE" | "PE",
  "moneyness": "ITM" | "ATM" | "OTM",     # winner among ranked candidates
  "instrument_key": "...",
  "strike": 24500,
  "expiry": "2026-07-17",
  "spot_price": 24487.3,
  "alert_premium": 121.2,
  "entry_low": 120.0, "entry_high": 123.6,   # ATR-based width now, see dynamic_trade_levels()
  "stop_loss": 108.4, "target_1": 145.9, "target_2": 168.2,  # ATR-based now
  "grade": "A+",                       # trade_score.py's A+/A/B+/B (Phase 3)
  "trade_score": 92,                   # 0-100, trade_score.compute_trade_score() (Phase 3)
  "probability_pct": 91,               # Phase 3
  "confidence_score": 9.4,             # X/10, Phase 3
  "risk_score": 2.3,                   # X/10, higher = riskier, Phase 3
  "reward_score": 9.1,                 # X/10, Phase 3
  "expected_rr": "1:2.7",              # Phase 3
  "factor_checklist": ["Trend ✅", "Volume ✅", ...],  # Phase 3, for Telegram
  "confidence_stars": "★★★★★",         # backward-compat display field, still populated
  "confidence_label": "Very High",     # backward-compat display field, still populated
  "risk_rating": "🟡",
  "risk_label": "Medium",
  "expected_holding_minutes": (20, 40),
  "why_reasons": ["EMA aligned", "Above VWAP", "Strong OI Build-up", ...],
  "session": "10:35-11:35",
  "alternatives": [ {symbol, direction, moneyness, grade, trade_score}, ... ],  # text-only
}
"""

from datetime import datetime, timezone, timedelta

import market_data
import instrument_master
from logging_setup import get_logger
from exceptions import ConfigurationError
from config import (
    SESSION_WINDOWS, STRIKE_STEP, STRIKE_RANGE, SYMBOLS,
    ENTRY_SCORE, T1_ATR_MULT,
)

from indicators import (
    ema_trend_direction, compute_atr, compute_ema, market_regime, find_nearby_level,
    momentum_pct, efficiency_ratio,
)
from option_chain import (
    UNDERLYING_INSTRUMENT_KEYS, current_expiry, _extract_leg,
    vwap_check, vwap_distance_pct, multi_timeframe_confirms, directional_bias,
    get_atm_strike, resolve_underlying_key, days_to_expiry,
)
from filters import (
    oi_is_building, oi_change_pct, liquidity_ok, delta_acceptable, expiry_theta_ok,
    quality_gate, reasons_from_factors,
)
from levels import dynamic_trade_levels, estimated_holding_minutes
# Phase 3: Professional Trade Score Engine (replaces grading.py's binary-
# strengths example_quality_score() in this file's active path -- grading.py
# itself is left in place, unused, as a reference/fallback).
import trade_score
from ranking import _grade_rank_key, rank_all

_logger = get_logger("scanner")

IST = timezone(timedelta(hours=5, minutes=30))

# Per-PROCESS cache (one Actions run = one process, same rationale as
# option_chain._vwap_cache): NIFTY's own 15-min efficiency ratio is
# read once per run for trade_score.market_strength(), not once per
# strike -- every symbol/direction/moneyness this run shares the same
# broad-market conviction read, and it doesn't meaningfully change
# within one ~5-minute cycle anyway.
_market_strength_cache = {}


def _broad_market_efficiency_ratio() -> float:
    """
    Phase 3: NIFTY's own 15-min efficiency_ratio() as the "Market
    Strength" factor -- independent of whatever symbol/direction is
    currently being scored. Falls back to 0.0 (neutral, not a crash)
    on any data problem, same "don't take down the whole run over one
    soft signal" posture as opportunity_score._safe_total_oi().
    """
    if "value" in _market_strength_cache:
        return _market_strength_cache["value"]
    try:
        nifty_key = UNDERLYING_INSTRUMENT_KEYS["NIFTY"]
        candles = market_data.get_recent_underlying_candles(nifty_key, "15minute", 30)
        value = efficiency_ratio(candles) if candles else 0.0
    except Exception as exc:
        _logger.info(f"MARKET_STRENGTH: NIFTY efficiency-ratio read failed ({type(exc).__name__}: {exc}) -- scoring as neutral (0.0)")
        value = 0.0
    _market_strength_cache["value"] = value
    return value


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


def resolve_strike_candidates(symbol: str, direction: str, atm_strike, spot_price: float, expiry: str) -> dict:
    """
    Phase 2 (dynamic strike selection). Returns real per-bucket strike
    lists: {"ITM": [...], "ATM": [strike], "OTM": [...]} -- sourced
    from instrument_master.py's actual listed strikes whenever
    available, so this never assumes uniform spacing the way the old
    `atm_strike + offset_steps * STRIKE_STEP[symbol]` arithmetic did
    (a real gap for individual stock options -- see config.py's own
    "unverified"/"a guess" comments on STOCK_STRIKE_STEP).

    FALLBACK, not a hard dependency: if instrument_master has nothing
    for this symbol/expiry today (premarket.yml's cache step hasn't
    run yet, a genuinely thin/delisted symbol, or any lookup error),
    this degrades to the exact pre-Phase-2 fixed-step behavior via
    moneyness_offsets() + STRIKE_STEP -- a bad cache day means "back to
    how this worked before", never "zero candidates for the whole
    symbol". This is what makes Phase 2 additive rather than a new
    single point of failure.
    """
    try:
        offsets = instrument_master.nearest_atm_and_offsets(
            symbol, spot_price, n_each_side=STRIKE_RANGE, expiry=expiry,
        )
    except Exception as exc:
        _logger.warning(
            f"STRIKE_ENGINE {symbol}: instrument_master lookup failed "
            f"({type(exc).__name__}: {exc}) -- falling back to fixed STRIKE_STEP arithmetic"
        )
        offsets = None

    if offsets is not None:
        # instrument_master returns unsigned "LOWER"/"HIGHER" walks from
        # ATM by strike value. For a CE, ITM = lower strikes (already
        # in-the-money), OTM = higher strikes; for a PE it's reversed --
        # same ITM/OTM-per-direction convention moneyness_offsets()
        # already encodes for the legacy path below.
        if direction == "CE":
            return {"ITM": offsets["LOWER"], "ATM": offsets["ATM"], "OTM": offsets["HIGHER"]}
        return {"ITM": offsets["HIGHER"], "ATM": offsets["ATM"], "OTM": offsets["LOWER"]}

    step = STRIKE_STEP.get(symbol)
    if step is None:
        # Same guard as option_chain.get_atm_strike()'s fallback -- see
        # that function's docstring. In practice this function is only
        # ever called right after get_atm_strike() already succeeded
        # for the same symbol+expiry (detect_setups() below), so
        # STRIKE_STEP[symbol] having a value here is usually already
        # guaranteed by that earlier call NOT raising -- this guard is
        # defensive insurance in case that call ordering ever changes,
        # not the primary safety net.
        raise ConfigurationError(
            f"{symbol}: no instrument_master strikes AND no STRIKE_STEP fallback "
            f"entry -- cannot resolve strike candidates for this symbol today."
        )
    buckets = moneyness_offsets(direction)
    return {
        moneyness: [atm_strike + offset_steps * step for offset_steps in offset_list]
        for moneyness, offset_list in buckets.items()
    }


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
    oi_delta_pct = oi_change_pct(oi_history, instrument_key, oi)  # read BEFORE oi_is_building mutates oi_history
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
    # placeholder for the OLD binary "reasons" factor only --
    # trade_score.volume_strength() below is a genuinely continuous read
    # (raw option volume vs. the liquidity engine's own minimum), so the
    # Trade Score's Volume factor isn't just this same binary reused
    # again.

    # Levels are computed here (pre-grade) rather than after grading, as
    # in the old flow -- pure ATR arithmetic, no extra API calls, so the
    # (small) extra compute on strikes that end up "Reject" is cheap, and
    # trade_score's risk_reward/reward/expected-RR all need premium-terms
    # target/SL distances from these SAME capped levels (not the pre-cap
    # provisional_t1_distance above, which is index-points, not premium,
    # and only intended for the risk_reward_ok gate input).
    levels = dynamic_trade_levels(premium, atr_index_points, delta, direction, spot_price, nearby_level)

    ema9 = compute_ema([c["close"] for c in candles_15m], 9)
    ema21 = compute_ema([c["close"] for c in candles_15m], 21)
    raw_momentum = momentum_pct(candles_15m)
    signed_momentum = None
    if raw_momentum is not None:
        signed_momentum = raw_momentum if direction == "CE" else -raw_momentum
    signed_vwap_dist = vwap_distance_pct(symbol, spot_price, direction)
    target_distance = levels["target_1"] - premium
    sl_distance = premium - levels["stop_loss"]

    strengths = {
        "trend": trade_score.trend_strength(efficiency_ratio(candles_15m)),
        "volume": trade_score.volume_strength(volume, symbol),
        "oi": trade_score.oi_build_strength(oi_delta_pct),
        "momentum": trade_score.momentum_strength(signed_momentum),
        "vwap": trade_score.vwap_strength(signed_vwap_dist),
        "ema": trade_score.ema_strength(ema9, ema21, spot_price) if ema9 is not None and ema21 is not None else 0.0,
        "risk_reward": trade_score.risk_reward_strength(target_distance, sl_distance),
        "liquidity": trade_score.liquidity_strength(oi, volume, bid, ask, premium, symbol),
        "market_strength": trade_score.market_strength(_broad_market_efficiency_ratio()),
        "htf": trade_score.htf_strength(mtf_confirmed),
    }
    score = trade_score.compute_trade_score(strengths)
    grade = trade_score.compute_grade(score)

    _logger.info(
        f"SCORE {symbol} {direction} {moneyness} strike={strike} "
        f"trade_score={score}/100 grade={grade} (entry_floor={ENTRY_SCORE}) | "
        + " ".join(f"{k}={v:.2f}" for k, v in strengths.items())
    )

    if grade == "Reject":
        return None

    stars, confidence_label = trade_score.confidence_display(grade)
    expiry_days = days_to_expiry(expiry)
    atr_pct = (atr_index_points / spot_price) if spot_price else 0.0
    risk_score = trade_score.compute_risk_score(atr_pct, moneyness, expiry_days)
    risk_emoji, risk_label = trade_score.risk_emoji_label(risk_score)
    rr_ratio = (target_distance / sl_distance) if sl_distance else 0.0
    reward_score = trade_score.compute_reward_score(rr_ratio)
    probability_pct = trade_score.compute_probability_pct(score)
    confidence_score = trade_score.compute_confidence_score(score)
    expected_rr = trade_score.expected_rr_text(premium, levels["stop_loss"], levels["target_1"])
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
        # Phase 3: new numeric fields shown in Telegram (per an explicit
        # decision reversing the earlier "hide all numbers" design).
        "trade_score": score,
        "probability_pct": probability_pct,
        "confidence_score": confidence_score,
        "risk_score": risk_score,
        "reward_score": reward_score,
        "expected_rr": expected_rr,
        "factor_checklist": trade_score.factor_checklist(strengths),
        # Backward-compat display fields, still populated:
        "confidence_stars": stars,
        "confidence_label": confidence_label,
        "risk_rating": risk_emoji,
        "risk_label": risk_label,
        "expected_holding_minutes": holding_minutes,
        "why_reasons": reasons_from_factors(factors),
        "session": session,
        "alternatives": [],  # filled in by main.py's run_scanner_step, not here
        "_quality_score": score,  # ranking.py's tiebreak key -- now the Trade Score itself
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
    min_quality_score = ENTRY_SCORE  # floor matching the lowest grade, "B" (trade_score.py, Phase 3)
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
        expiry = current_expiry(symbol)
        buckets = resolve_strike_candidates(symbol, direction, atm_strike, spot_price, expiry)

        for moneyness, strike_list in buckets.items():
            bucket_setups = []
            for strike in strike_list:
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
        # CRITICAL BUGFIX (live failure, 2026-07-17): this used to let
        # a single symbol's exception propagate straight out of this
        # function -- which meant one broken symbol (a stock's futures
        # contract not resolving, a stale instrument master row,
        # anything specific to that ONE symbol) killed the ENTIRE run,
        # discarding every candidate already scored for every OTHER
        # symbol that ran before it in this loop. Confirmed live: NIFTY
        # scored 85/100 "Strong Entry" and BANKNIFTY scored 70/100
        # "Entry" -- both should have alerted -- but PNB (later in
        # SYMBOLS) crashed with InstrumentNotFound and the whole run
        # aborted before main.py ever saw either result. Now: log the
        # failing symbol and its error, then continue to the next
        # symbol, so the rest of the run's alerts are never held
        # hostage by one symbol's data problem.
        try:
            all_candidates.extend(detect_setups(symbol, oi_history))
        except Exception as e:
            _logger.error(
                f"SYMBOL_FAILED {symbol}: {type(e).__name__}: {e} -- "
                f"skipping this symbol for this run, continuing with the rest"
            )

    return rank_all(all_candidates)
