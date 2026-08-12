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
    ATR_CHASE_FILTER_ENABLED, ATR_CHASE_MULTIPLIER,
)

from indicators import (
    ema_trend_direction, compute_atr, compute_ema, market_regime, find_nearby_level,
    momentum_pct, efficiency_ratio,
)
from option_chain import (
    UNDERLYING_INSTRUMENT_KEYS, current_expiry, _extract_leg,
    vwap_check, vwap_distance_pct, directional_bias,
    get_atm_strike, resolve_underlying_key, days_to_expiry,
)
from filters import (
    oi_is_building, oi_change_pct, liquidity_ok, delta_acceptable, expiry_theta_ok,
    quality_gate, reasons_from_factors, rejection_reasons,
    candles_ready, premium_in_range, directional_confirmation_status,
)
from levels import dynamic_trade_levels, estimated_holding_minutes
# Phase 3: Professional Trade Score Engine (replaces grading.py's binary-
# strengths example_quality_score() in this file's active path -- grading.py
# itself is left in place, unused, as a reference/fallback).
import trade_score
from ranking import _grade_rank_key, rank_all
# Part 5: Options Intelligence Engine -- additive only (see that
# package's engine.py module docstring for the full integration
# contract). Computed AFTER the existing hard gate + Trade Score both
# already passed, using data score_strike() already fetched this cycle
# -- no new broker call, no duplicated OI/liquidity/Greeks math.
from app.engines.options_intelligence import engine as options_intel
from config import OPTIONS_INTELLIGENCE_FILTER_ENABLED
# Candlestick Pattern Recognition -- additive only, same convention as
# Options Intelligence above. Pure arithmetic on candles_15m, which
# score_strike() already fetches a few lines below for
# ema_trend_direction()/find_nearby_level()/efficiency_ratio() -- no
# new broker call, no new candle history. See candlestick_patterns.py's
# module docstring and config.py's CANDLESTICK_* block.
import candlestick_patterns
from config import CANDLESTICK_PATTERNS_ENABLED

_logger = get_logger("scanner")

# Reset and populated once per detect_all_setups() call -- read back via
# get_last_scan_stats() right after main.py calls detect_all_setups(),
# so the EOD summary can report Rejected Signals without changing
# detect_all_setups()'s return type (still just the ranked candidate
# list, same as every other caller/spec expects).
_rejection_count = 0
_last_scan_stats = {}

IST = timezone(timedelta(hours=5, minutes=30))

# Per-PROCESS cache (one Actions run = one process, same rationale as
# option_chain._vwap_cache): NIFTY's own 15-min efficiency ratio is
# read once per run for trade_score.market_strength(), not once per
# strike -- every symbol/direction/moneyness this run shares the same
# broad-market conviction read, and it doesn't meaningfully change
# within one ~5-minute cycle anyway.
_market_strength_cache = {}


def _broad_market_efficiency_ratio():
    """
    Phase 3: NIFTY's own 15-min efficiency_ratio() as the "Market
    Strength" factor -- independent of whatever symbol/direction is
    currently being scored.

    BUGFIX (2026-08-07): used to fall back to 0.0 ("neutral") both on
    a data problem AND, silently, whenever NIFTY itself didn't have
    enough candles yet (indicators.efficiency_ratio() used to always
    return a float, never None) -- now that it can genuinely return
    None, this returns None too, all the way through, so
    trade_score.market_strength() correctly excludes it instead of
    scoring "no data" as "confirmed no market conviction".
    """
    if "value" in _market_strength_cache:
        return _market_strength_cache["value"]
    try:
        nifty_key = UNDERLYING_INSTRUMENT_KEYS["NIFTY"]
        candles = market_data.get_recent_underlying_candles(nifty_key, "15minute", 30)
        value = efficiency_ratio(candles) if candles else None
    except Exception as exc:
        _logger.info(
            f"MARKET_STRENGTH: NIFTY efficiency-ratio read failed ({type(exc).__name__}: {exc}) -- scoring as unavailable (None)",
            exc_info=True,
        )
        value = None
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
            f"({type(exc).__name__}: {exc}) -- falling back to fixed STRIKE_STEP arithmetic",
            exc_info=True,
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
    # Declared once, at the top, for the whole function -- a `global`
    # statement must textually precede every use of the name in the
    # function it's declared in (see the "SyntaxError: name
    # '_rejection_count' is assigned to before global declaration"
    # note further below, from the one time this WASN'T done and broke
    # the whole module import). Simplest way to guarantee that as more
    # rejection points were added over time (2026-08-10: Candle
    # Readiness Gate and ATR Chase Filter now also increment this,
    # previously they didn't -- see config.MIN_ALERT_PROBABILITY's
    # sibling note on why an accurate rejected-signal count matters for
    # the daily summary) is one declaration here, before anything else.
    global _rejection_count
    underlying_key = resolve_underlying_key(symbol)
    expiry = current_expiry(symbol)

    chain = market_data.get_option_chain(underlying_key, expiry)
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

    # --- Contract Resolution Assertion (2026-08-10) ---
    # Confirms the leg _extract_leg() just returned actually matches
    # what was requested (symbol/expiry/strike/side) -- logged
    # explicitly so a future instrument-master/expiry-mapping bug is
    # visible immediately here, instead of needing to be inferred from
    # downstream IV/premium numbers looking "off" for the strike (the
    # exact investigation this replaces -- see IV_SAMPLE's own docstring
    # in upstox.py: that log line is a chain-wide diagnostic sample,
    # unrelated to any one traded leg; THIS line is the one that
    # actually describes the strike being scored). _extract_leg() already
    # matches strictly on `row.get("strike_price") == strike` with the
    # correct call_/put_ prefix for `direction` (see its own docstring in
    # option_chain.py), so instrument_key here is not expected to ever
    # resolve to a different strike than requested -- this line makes
    # that guarantee visible rather than assumed.
    _logger.info(
        f"CONTRACT_RESOLUTION {symbol} requested=(direction={direction} strike={strike} expiry={expiry}) "
        f"resolved=(instrument_key={instrument_key} ltp={premium} delta={delta})"
    )

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

    # --- Candle Readiness Gate (2026-08-07, filters.candles_ready()) ---
    # NEW hard gate, checked before anything else is computed: a strike
    # is only scored once EITHER timeframe has enough COMPLETED candles
    # for momentum/trend/EMA to mean something. This directly targets
    # the candles_fetched=1/ema_ready=False/htf_ready=False/trend=0/
    # momentum=0-yet-score-45-55 pattern from the SCORE/GATE logs --
    # previously nothing stopped scoring from happening on that data at
    # all; see filters.candles_ready()'s own docstring for the full
    # rationale. candles_5m is fetched ONLY for this readiness check
    # (cached per (instrument_key, "5minute") by market_data.py, and
    # option_chain.directional_bias() already warms that same cache
    # earlier in detect_setups() for most runs, so this is rarely a new
    # broker call in practice).
    candles_5m = market_data.get_recent_underlying_candles(underlying_key, "5minute", 30)
    if not candles_ready(len(candles_5m), len(candles_15m)):
        _rejection_count += 1
        _logger.info(
            f"SKIPPED {symbol} {direction} {moneyness} strike={strike} - "
            f"Insufficient candle history (5m={len(candles_5m)}, 15m={len(candles_15m)})."
        )
        return None

    # Candlestick Pattern Recognition -- reuses candles_15m fetched just
    # above, no new broker call. Computed unconditionally when enabled
    # (advisory only: never gates this strike -- see candlestick_patterns.py
    # module docstring). Wrapped defensively even though analyze() itself
    # never raises, same "an optional add-on must never cost us an
    # otherwise-valid candidate" posture as options_intel below.
    if CANDLESTICK_PATTERNS_ENABLED:
        try:
            candlestick_read = candlestick_patterns.analyze(candles_15m, direction)
        except Exception as cs_exc:  # noqa: BLE001
            _logger.error(
                f"CANDLESTICK_PATTERNS_FAILED {symbol} {direction} {moneyness} strike={strike}: "
                f"{type(cs_exc).__name__}: {cs_exc}", exc_info=True,
            )
            candlestick_read = {"enabled": False, "error": True, "reason": str(cs_exc)}
    else:
        candlestick_read = {"enabled": False, "reason": "CANDLESTICK_PATTERNS_ENABLED is False"}

    # FIX 2 (ATR warmup, 2026-07-30, config.WARMUP_CANDLE_CONTINUITY_ENABLED):
    # a SEPARATE candle set, used ONLY as input to compute_atr() below.
    # candles_15m itself (used for ema_trend_direction, find_nearby_level,
    # momentum_pct, efficiency_ratio just below) is intentionally left
    # completely untouched -- Opening Range, today's High/Low, Session
    # VWAP, and breakout-level logic can never see a previous-session
    # candle through this path. When the flag is off (default) or it's
    # past the warmup cutoff, atr_candles IS candles_15m (same object).
    atr_candles, combined_candle_count = market_data.get_atr_warmup_candles(
        underlying_key, "15minute", candles_15m
    )
    # BUGFIX (2026-08-11): get_atr_warmup_candles()'s own docstring has
    # always said its result is "meant ONLY to feed compute_atr() (and,
    # at the same call site, compute_ema())" -- but the EMA calls below
    # were passing candles_15m (today-only) instead of atr_candles
    # (previous-session tail candles PREPENDED, when
    # WARMUP_CANDLE_CONTINUITY_ENABLED and before the cutoff -- see
    # that function's docstring). Confirmed live: combined_candle_count
    # was already 24-26 by 10:20-10:55 IST (well over EMA21's 21-candle
    # need) while candles_fetched (today-only) was still just 4-6 --
    # meaning EMA9/21 had enough REAL data available the entire time,
    # it just wasn't being handed to it. This made "EMA (15m): Not
    # Ready" mathematically unavoidable until today's own candles alone
    # reached 21 (~14:30 IST, 21 x 15min after a 9:15 open) instead of
    # using the warmup-merged data that was already sufficient from
    # shortly after market open. Same fix applied to the 5-min warmup
    # fetch below for m5_direction, which had an identical gap on the
    # 5-min timeframe (21-candle need -> ~11:00 IST on today-only data).
    #
    # NOTE (still true after this fix, flagging for awareness -- not
    # changed here): ATR_WARMUP_CUTOFF_HOUR/MINUTE (11:00 IST) stops
    # the warmup merge for the 15-min timeframe at that time regardless
    # of whether today's own 15-min candles have reached 21 yet (they
    # haven't -- that only happens at ~14:30 IST) -- so there is still
    # a gap window (roughly 11:00-14:30 IST) where 15-min EMA can go
    # from ready (warmup-assisted) back to not-ready (today-only,
    # insufficient) until today's own count catches up. The 5-min
    # warmup fetch has the same cutoff-vs-need mismatch on its own
    # timeframe. Both are pre-existing (the cutoff wasn't introduced by
    # this fix), left as-is pending a decision on whether/how to retune
    # ATR_WARMUP_CUTOFF_HOUR -- flagging rather than silently changing
    # a tuned threshold.
    candles_5m_warmup, _m5_combined_count = market_data.get_atr_warmup_candles(
        underlying_key, "5minute", candles_5m
    )

    ema_direction = ema_trend_direction(atr_candles)
    # 5-min EMA direction read (2026-08-10) -- a genuinely SEPARATE
    # computation from ema_direction above (15-min), reusing candles_5m
    # already fetched for the Candle Readiness Gate above (now warmup-
    # merged too, see BUGFIX note above). Feeds ONLY the Mandatory
    # Structural Confirmation check in filters.
    # directional_confirmation_status() -- see that function's
    # docstring for why this exists (trend/vwap alone were satisfying
    # the confirmation-count gate with zero real EMA-structure
    # evidence on either timeframe).
    m5_direction = ema_trend_direction(candles_5m_warmup)
    ema_aligned = ema_direction == direction
    ema9 = compute_ema([c["close"] for c in atr_candles], 9)
    ema21 = compute_ema([c["close"] for c in atr_candles], 21)
    vwap_confirmed = vwap_check(symbol, spot_price, direction)
    volume_confirmed = liquidity_ok(oi, volume, bid, ask, premium, symbol)  # see NOTE below
    oi_delta_pct = oi_change_pct(oi_history, instrument_key, oi)  # read BEFORE oi_is_building mutates oi_history
    oi_building = oi_is_building(oi_history, instrument_key, oi)
    liquidity_pass = liquidity_ok(oi, volume, bid, ask, premium, symbol)
    # BUGFIX (2026-08-11): mtf_confirmed/mtf_confirmed_tristate used to
    # come from multi_timeframe_confirms()/multi_timeframe_confirmation_detail()
    # (option_chain.py), which do their OWN separate 15-min candle
    # fetch for this same symbol -- via get_recent_underlying_candles()
    # directly, which never sees warmup candles (only
    # get_atr_warmup_candles() does -- see that function's own
    # docstring). That made "HTF" a REDUNDANT read of the exact same
    # 15-min EMA9/21 crossover ema_direction above already computes
    # (now correctly warmup-aware, see the ema_direction/atr_candles
    # BUGFIX note above) -- just via a second, un-warmed fetch that
    # stayed "Not Ready" long after ema_direction itself was already
    # confirming. Deriving both directly from ema_direction here
    # instead: same underlying signal, single warmup-aware source, and
    # the GATE log line's "mtf_confirmed=Y/N" now can't disagree with
    # what the Directional Confirmation Gate/htf_strength() actually
    # used to decide. multi_timeframe_confirms()/
    # multi_timeframe_confirmation_detail() themselves are left
    # untouched in option_chain.py (existing public API, unused
    # elsewhere per a repo-wide check) in case a future caller wants
    # the un-warmed read specifically.
    mtf_confirmed_tristate = (ema_direction == direction) if ema_direction is not None else None
    mtf_confirmed = bool(mtf_confirmed_tristate)
    delta_ok = delta_acceptable(moneyness, delta)
    expiry_ok = expiry_theta_ok(moneyness, delta, expiry)
    premium_ok = premium_in_range(symbol, premium)

    # Trend/momentum, moved up from the strengths dict below (2026-08-07)
    # -- both are now needed earlier, by the Directional Confirmation Gate,
    # and are reused as-is when strengths{} is built further down (no
    # duplicate computation).
    trend_value = efficiency_ratio(candles_15m)
    raw_momentum = momentum_pct(candles_15m)
    signed_momentum = None
    if raw_momentum is not None:
        signed_momentum = raw_momentum if direction == "CE" else -raw_momentum

    nearby_level = find_nearby_level(candles_15m, direction)
    atr_index_points = compute_atr(atr_candles)

    # --- Diagnostics (TEMPORARY -- remove once EMA/ATR-unavailable
    # rejections are confirmed resolved in production; see FIX 1/FIX 2
    # deliverables notes) ---
    _logger.info(
        f"DIAG {symbol} {direction} {moneyness} strike={strike} | "
        f"candles_fetched={len(candles_15m)} combined_candle_count={combined_candle_count} "
        f"ema_ready={ema9 is not None and ema21 is not None} "
        f"atr_ready={atr_index_points is not None} htf_ready={bool(mtf_confirmed_tristate)}"
    )

    if atr_index_points is None or delta == 0:
        _logger.info(f"SKIP {symbol} {direction} {moneyness} strike={strike}: ATR unavailable or delta==0")
        return None  # dynamic_trade_levels() would raise on this -- bail before it does

    # --- ATR Chase Filter (2026-08-07, config.ATR_CHASE_FILTER_ENABLED) ---
    # Rejects a strike when the underlying has already travelled more
    # than ATR_CHASE_MULTIPLIER x today's ATR away from today's session
    # open by the time this cycle runs -- buying an option after the
    # move that would have justified it has already happened is exactly
    # the "chasing an extended candle" pattern that tends to stop out.
    # "Today's session open" = candles_15m[0]["open"] -- candles_15m is
    # today-only (see the FIX 2/ATR-warmup comment above: atr_candles,
    # not candles_15m, is the one that may carry previous-session tail
    # candles), so this is genuinely today's first candle, not
    # yesterday's close.
    if ATR_CHASE_FILTER_ENABLED:
        session_open = candles_15m[0]["open"]
        move_from_open = abs(spot_price - session_open)
        chase_ceiling = atr_index_points * ATR_CHASE_MULTIPLIER
        if move_from_open > chase_ceiling:
            _rejection_count += 1
            _logger.info(
                f"REJECTED {symbol} {direction} {moneyness} strike={strike} | "
                f"Reasons: Price extended beyond ATR chase ceiling "
                f"(move={move_from_open:.2f}, ceiling={chase_ceiling:.2f} = "
                f"{ATR_CHASE_MULTIPLIER}x ATR)"
            )
            return None

    # Provisional T1 distance (pre-cap) just to test room against the
    # nearby level -- dynamic_trade_levels() below recomputes/caps this
    # properly; this is only for the risk_reward_ok gate input.
    #
    # CLARIFICATION (2026-08-10): this "risk_reward_ok" and
    # strengths["risk_reward"] (trade_score.risk_reward_strength(),
    # computed further below from dynamic_trade_levels()'s FINAL
    # entry/stop/target numbers) are two DIFFERENT, both-legitimate
    # checks that got confusingly similar names -- not one canonical
    # R:R with a bug. This one ("level room check") asks "is there
    # enough index-point distance to the nearest support/resistance
    # level, BEFORE dynamic_trade_levels() caps the target against
    # that same level, to justify entering at all". risk_reward_strength()
    # asks "given the FINAL, already-capped entry/stop/target1, how good
    # is the resulting ratio against TS_RR_BASELINE". A trade can
    # legitimately fail one and pass the other -- e.g. plenty of room to
    # the level (this check passes) but the final capped target still
    # sits close to entry relative to the stop (risk_reward_strength()
    # scores low) is a completely coherent, non-contradictory outcome.
    # The RR log line right after this block prints both explicitly so
    # that's visible in the log instead of looking like two disagreeing
    # copies of "the same" number.
    provisional_t1_distance = atr_index_points * abs(delta) * T1_ATR_MULT
    if nearby_level is not None:
        index_distance_to_level = abs(nearby_level - spot_price)
        risk_reward_ok = (index_distance_to_level * abs(delta)) > provisional_t1_distance
    else:
        risk_reward_ok = True  # no level in the lookback window -- don't gate on absence of data
    _logger.info(
        f"RR_LEVEL_CHECK {symbol} {direction} {moneyness} strike={strike} | "
        f"nearby_level={nearby_level} distance_to_level_pts={index_distance_to_level if nearby_level is not None else None} "
        f"provisional_t1_distance_pts={provisional_t1_distance:.2f} risk_reward_ok(level_room)={'Y' if risk_reward_ok else 'N'} "
        f"-- see strengths['risk_reward'] in the SCORE line below for the SEPARATE final-levels ratio"
    )

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
        "premium_in_range_ok": premium_ok,
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
        _rejection_count += 1
        _logger.info(
            f"REJECTED {symbol} {direction} {moneyness} strike={strike} | "
            f"Reasons: {', '.join(rejection_reasons(factors))}"
        )
        return None

    # --- Directional Confirmation Gate (2026-08-07,
    # config.DIRECTIONAL_CONFIRMATION_ENABLED) ---
    # NEW hard gate, separate from quality_gate()'s 3 risk checks above:
    # requires at least DIRECTIONAL_CONFIRMATION_MIN_COUNT of {EMA
    # aligned, HTF confirmed, trend above threshold, momentum above
    # threshold, VWAP confirmed} before a strike proceeds to scoring.
    # Deliberately NOT "all must pass" -- see
    # filters.directional_confirmation_status()'s docstring for why.
    directional_status = directional_confirmation_status(
        ema_direction, mtf_confirmed_tristate, trend_value, signed_momentum,
        vwap_confirmed, direction, m5_direction=m5_direction,
    )
    if not directional_status["passed"]:
        # `global _rejection_count` is declared once, at the top of
        # this function (see that declaration's own comment for why).
        _rejection_count += 1
        _logger.info(
            f"REJECTED {symbol} {direction} {moneyness} strike={strike} | "
            f"Reasons: {'; '.join(trade_score.rejection_detail_lines(True, directional_status, 0, 0, False))}"
        )
        return None

    # REMOVED (2026-08-10, "do we need all this" review): a Mandatory
    # Core Evidence Check used to live here (trend/EMA(15m)/HTF all
    # None -> reject). Removed as redundant AND contradictory with the
    # Mandatory Structural Confirmation check the Directional
    # Confirmation Gate above already performs (filters.
    # directional_confirmation_status()'s structural_ok, which also
    # considers m5_direction/5-min EMA): a strike confirmed via 5-min
    # EMA alone (real evidence) could pass that gate and then get
    # rejected here anyway, purely because this check never looked at
    # m5_direction -- confirmed live via direct simulation. The
    # Directional Confirmation Gate already enforces "is there real
    # directional evidence" more precisely (across 15m EMA, 5m EMA, and
    # HTF, not just 15m EMA + HTF) -- this check added nothing that
    # gate didn't already cover, only a false-rejection risk.

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

    signed_vwap_dist = vwap_distance_pct(symbol, spot_price, direction)
    target_distance = levels["target_1"] - premium
    sl_distance = premium - levels["stop_loss"]

    strengths = {
        # trend/signed_momentum: computed earlier (see Directional
        # Confirmation Gate above) -- reused here, not recomputed.
        "trend": trade_score.trend_strength(trend_value),
        "volume": trade_score.volume_strength(volume, symbol),
        "oi": trade_score.oi_build_strength(oi_delta_pct),
        "momentum": trade_score.momentum_strength(signed_momentum),
        "vwap": trade_score.vwap_strength(signed_vwap_dist),
        # BUGFIX (2026-07-30): was `else 0.0` -- that made "EMA not yet
        # computable" (e.g. before enough candles exist) score
        # identically to "EMA confirmed and pointing the wrong way",
        # dragging the composite score down for a reason that had
        # nothing to do with the setup. None -> compute_trade_score()
        # excludes this factor and renormalizes the remaining weights.
        "ema": trade_score.ema_strength(ema9, ema21, spot_price) if ema9 is not None and ema21 is not None else None,
        "risk_reward": trade_score.risk_reward_strength(target_distance, sl_distance),
        "liquidity": trade_score.liquidity_strength(oi, volume, bid, ask, premium, symbol),
        "market_strength": trade_score.market_strength(_broad_market_efficiency_ratio()),
        # BUGFIX (2026-08-07): htf_strength() now takes the tri-state
        # mtf_confirmed_tristate (True/False/None) instead of the plain
        # bool -- "15-min EMA not yet computable" is excluded from the
        # weighted average instead of scored as confirmed-against.
        "htf": trade_score.htf_strength(mtf_confirmed_tristate),
    }
    score = trade_score.compute_trade_score(strengths)
    grade = trade_score.compute_grade(score)

    # --- Score Logic fix (2026-08-07) ---
    # BUGFIX: previously, Volume=Excellent + OI=Excellent + Liquidity=
    # Excellent could clear ENTRY_SCORE on their own (30 of the 90-100
    # available weighted points) while Trend/Momentum/HTF sat at a hard
    # 0 -- see indicators.efficiency_ratio()'s and this module's None-
    # exclusion fixes above, which stop that hard-0 from happening in
    # the first place for data that's merely unavailable. The
    # Directional Confirmation Gate above is the other half of this fix:
    # even once real (non-None, genuinely weak) trend/momentum/htf
    # readings bring the score up into the 45-55 range on liquidity
    # alone, the gate has already rejected that strike before this
    # point unless at least 2 of the 5 directional signals genuinely
    # confirmed. The two fixes are complementary, not redundant: this
    # one stops the score from lying about missing data, the gate stops
    # a genuinely-weak-but-technically-nonzero setup from alerting on
    # liquidity alone.
    candlestick_penalty_applied = False
    original_score = score
    score, candlestick_penalty_applied = trade_score.apply_candlestick_penalty(score, candlestick_read)
    if candlestick_penalty_applied:
        grade = trade_score.compute_grade(score)

    _logger.info(
        f"SCORE {symbol} {direction} {moneyness} strike={strike} "
        f"trade_score={score}/100 grade={grade} (entry_floor={ENTRY_SCORE}"
        + (f", pre_candlestick_penalty={original_score}" if candlestick_penalty_applied else "")
        + ") | "
        # FIX (2026-07-31): strengths.items() can legitimately contain
        # None -- compute_trade_score() above already documents and
        # handles this ("None for a factor that's genuinely unavailable
        # this cycle e.g. EMA9/21 not ready"), but this log line
        # didn't, and blindly applying :.2f to None crashed with
        # "unsupported format string passed to NoneType.__format__" --
        # confirmed live, taking out every single symbol for the whole
        # cycle during the ATR-ready-but-EMA-not-ready window (roughly
        # candles 15-20, ~90 minutes around midday IST). This was
        # pre-existing (unrelated to today's other changes) and just
        # hadn't been hit by a real run until now.
        + " ".join(f"{k}={v:.2f}" if v is not None else f"{k}=None"
                    for k, v in strengths.items())
    )
    if candlestick_read.get("enabled") and candlestick_read.get("patterns_detected"):
        _logger.info(
            f"CANDLESTICK {symbol} {direction} {moneyness} strike={strike} "
            f"patterns={candlestick_read['patterns_detected']} "
            f"dominant={candlestick_read.get('dominant_pattern')} "
            f"alignment={candlestick_read.get('alignment_score_pct')}"
        )

    # REMOVED (2026-08-10, "do we need all this" review): an Adaptive
    # Entry Score (trade_score.effective_entry_score(), a SEPARATE
    # session-time/trend-aware floor: 50/55/65 depending on time of
    # day and this strike's own trend reading) used to be computed and
    # gated on here. Removed because detect_setups() (the caller, one
    # level up) ALREADY applies its own independent, regime-aware floor
    # (config.ENTRY_SCORE + config.REGIME_SCORE_PENALTY[regime], via
    # market_regime() -- see detect_setups()'s own min_quality_score
    # variable) to every candidate this function returns. The two
    # floors were computed completely independently and could
    # disagree -- e.g. a strike passing THIS function's old 50-point
    # strong-trend floor could still get silently dropped by
    # detect_setups()'s 70-point VOLATILE-regime floor a few lines
    # later, with the SCORE log line here misleadingly showing
    # "entry_floor=50" for a strike that never actually became a
    # candidate. Consolidating to ONE floor (detect_setups()'s
    # regime-aware one) removes that contradiction; this function now
    # gates only on the static baseline (`grade == "Reject"`, i.e.
    # score < ENTRY_SCORE) so obviously-too-low-score strikes still
    # skip the remaining (levels/probability/Options Intelligence)
    # computation below, same efficiency reasoning as before, just
    # without a second, disagreeing adaptive threshold.
    if grade == "Reject":
        _logger.info(
            f"REJECTED {symbol} {direction} {moneyness} strike={strike} | "
            f"Reasons: {'; '.join(trade_score.rejection_detail_lines(True, directional_status, score, ENTRY_SCORE, True))}"
        )
        return None

    stars, confidence_label = trade_score.confidence_display(grade)
    expiry_days = days_to_expiry(expiry)
    atr_pct = (atr_index_points / spot_price) if spot_price else 0.0
    risk_score = trade_score.compute_risk_score(atr_pct, moneyness, expiry_days)
    risk_emoji, risk_label = trade_score.risk_emoji_label(risk_score)
    rr_ratio = (target_distance / sl_distance) if sl_distance else 0.0
    reward_score = trade_score.compute_reward_score(rr_ratio)
    probability_pct = trade_score.compute_probability_pct(score)
    probability_band = trade_score.probability_label(probability_pct)
    score_breakdown = trade_score.scoring_reasons(strengths)
    confidence_score = trade_score.compute_confidence_score(score)
    _logger.info(
        f"PROBABILITY {symbol} {direction} {moneyness} strike={strike} "
        f"probability={probability_pct}% ({probability_band}) | "
        + " | ".join(score_breakdown) + f" | Final Probability = {probability_pct}%"
    )
    expected_rr = trade_score.expected_rr_text(premium, levels["stop_loss"], levels["target_1"])
    holding_minutes = estimated_holding_minutes(atr_index_points, target_distance / abs(delta) if delta else 0)

    # Part 5: Options Intelligence Engine -- additive (see import comment
    # above). offset_index isn't tracked separately in this function today
    # (moneyness alone is), so 1 is passed as a reasonable single-strike
    # default; STRIKE_RANGE>1 callers that want the finer Deep ITM/Deep
    # OTM distinction should thread the real offset through once this is
    # promoted beyond opt-in.
    oi_intel = options_intel.analyze(
        symbol=symbol, direction=direction, moneyness=moneyness, offset_index=1,
        strike=strike, leg=leg, chain=chain, spot_price=spot_price, expiry=expiry,
        oi_history=oi_history, oi_delta_pct=oi_delta_pct,
    )
    if OPTIONS_INTELLIGENCE_FILTER_ENABLED and oi_intel.get("enabled") and not oi_intel.get("accepted", True):
        # NOTE (merge fix): `global _rejection_count` was already declared
        # earlier in this same function (see the hard_gate_pass branch
        # above); a function needs only one `global` statement per name.
        # Redeclaring it here produced a hard SyntaxError under Python
        # 3.12+ ("name '_rejection_count' is assigned to before global
        # declaration") -- this file could not be imported at all before
        # this fix. Removing the duplicate declaration changes nothing at
        # runtime: _rejection_count is still the same module-level global,
        # still incremented here exactly as before.
        _rejection_count += 1
        _logger.info(
            f"REJECTED {symbol} {direction} {moneyness} strike={strike} | "
            f"Reasons (Options Intelligence): {', '.join(oi_intel.get('rejection_reasons') or [])}"
        )
        return None

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
        "probability_label": probability_band,
        "score_breakdown": score_breakdown,
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
        "options_intelligence": oi_intel,  # Part 5, additive -- see app/engines/options_intelligence/
        "candlestick_patterns": candlestick_read,  # additive -- see candlestick_patterns.py
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
    # This is now the SOLE entry-score floor (2026-08-10, "do we need
    # all this" review) -- score_strike() used to also apply its own
    # separate, session-time/trend-aware adaptive floor
    # (trade_score.effective_entry_score(), since removed), which could
    # disagree with this regime-based one. Consolidated here: baseline
    # ENTRY_SCORE, adjusted up for a choppier/more volatile regime via
    # market_regime()'s penalty -- one floor, always regime-aware, no
    # separate session-time adjustment layered on top of it.
    min_quality_score = ENTRY_SCORE  # floor matching the lowest grade, "Watchlist" (trade_score.py, Phase 3/4)
    if underlying_key:
        candles_15m = market_data.get_recent_underlying_candles(underlying_key, "15minute", 30)
        regime, penalty = market_regime(candles_15m)
        min_quality_score += penalty

    biases = directional_bias(symbol)
    # Diagnostics (TEMPORARY -- see FIX 1/FIX 2 deliverables notes).
    _logger.info(f"DIAG {symbol}: bias_ready={bool(biases)}")
    if not biases:
        # As of the 2026-07-24 fix, directional_bias() no longer
        # returns [] on 5-min/15-min EMA disagreement (that's a score
        # penalty now, not a gate -- see option_chain.directional_bias()'s
        # docstring). This branch is only reached when the symbol's
        # underlying instrument itself can't be resolved at all -- a
        # genuinely absent/misconfigured symbol, not a bias question.
        _logger.info(f"BIAS {symbol}: underlying instrument could not be resolved -- no strikes scored this run")
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
    global _rejection_count
    _rejection_count = 0
    api_failures = 0
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
            api_failures += 1
            _logger.error(
                f"SYMBOL_FAILED {symbol}: {type(e).__name__}: {e} -- "
                f"skipping this symbol for this run, continuing with the rest",
                exc_info=True,
            )

    ranked = rank_all(all_candidates)
    _last_scan_stats.update({
        "scanned_symbols": len(SYMBOLS),
        "valid_signals": len(ranked),
        "rejected_signals": _rejection_count,
        "api_failures": api_failures,
    })
    return ranked


def get_last_scan_stats() -> dict:
    """
    Counts from the most recent detect_all_setups() call in this
    process -- scanned symbols, signals that passed the quality gate,
    signals rejected at the hard gate, and per-symbol exceptions
    swallowed by the try/except above. main.py reads this right after
    calling detect_all_setups() and folds it into state["daily_stats"]
    so the EOD summary can report it (Medium priority: EOD daily
    statistics). Returns a copy so callers can't mutate scanner.py's
    internal dict.
    """
    return dict(_last_scan_stats)
