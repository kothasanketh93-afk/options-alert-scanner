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
from state import trading_date_str
import instrument_master
from logging_setup import get_logger
from exceptions import ConfigurationError
from config import (
    SESSION_WINDOWS, STRIKE_STEP, STRIKE_RANGE, SYMBOLS,
    ENTRY_SCORE, T1_ATR_MULT,
    ATR_CHASE_FILTER_ENABLED, ATR_CHASE_MULTIPLIER,
    STRUCTURAL_EXTENSION_FILTER_ENABLED, STRUCTURAL_EXTENSION_MAX_DISTANCE_ATR,
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
from config import PROBABILITY_SHADOW_MODE_ENABLED

_logger = get_logger("scanner")

# Reset and populated once per detect_all_setups() call -- read back via
# get_last_scan_stats() right after main.py calls detect_all_setups(),
# so the EOD summary can report Rejected Signals without changing
# detect_all_setups()'s return type (still just the ranked candidate
# list, same as every other caller/spec expects).
_rejection_count = 0
_last_scan_stats = {}

# --- Phase-2 Shadow/Validation Mode (2026-08-23) ---
# See config.PROBABILITY_SHADOW_MODE_ENABLED's own comment for the
# full rationale. This buffer is purely additive: score_strike()'s
# LIVE return value (what feeds detect_setups()'s `ranked` list, and
# therefore main.py's `chosen` candidate) is COMPLETELY UNCHANGED by
# shadow mode -- a calibration-unavailable candidate still returns
# None to the live pipeline exactly as before. Shadow candidates are
# captured here, on the side, and drained once per scan cycle by
# main.py -- never merged into `ranked`, never able to become `chosen`,
# never able to block or displace a genuinely live-eligible candidate.
_shadow_candidates_this_cycle = []


def reset_shadow_candidates():
    """Called once per scan cycle by main.py, before scanning starts."""
    global _shadow_candidates_this_cycle
    _shadow_candidates_this_cycle = []


def get_shadow_candidates() -> list:
    """Called once per scan cycle by main.py, after scanning completes."""
    return list(_shadow_candidates_this_cycle)

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
        # BUGFIX (2026-09-25, same audit as candles_15m/momentum above):
        # today-only fetch, unwarmed -- efficiency_ratio() only needs
        # >=2 candles so the practical impact window is much smaller
        # (9:15-9:30 IST, one cycle) than the ATR/momentum cases, but
        # it's the same class of bug and the fix is free (same cached
        # mechanism), so applied here too for consistency.
        today_candles = market_data.get_recent_underlying_candles(nifty_key, "15minute", 30)
        candles, _ = market_data.get_atr_warmup_candles(nifty_key, "15minute", today_candles) if today_candles else ([], 0)
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
    premium = leg["ltp"]  # UNCHANGED (2026-08-15 P0 audit): this remains the ONLY
                           # price dynamic_trade_levels()/entry/SL/T1/T2/RR/scoring
                           # read -- see the evidence-only block immediately below,
                           # which is additive and never feeds back into `premium`.
    oi = leg["oi"]
    volume = leg["volume"]
    delta = leg["delta"]
    bid, ask = leg.get("bid"), leg.get("ask")

    # --- Bid/Ask Execution Evidence (2026-08-15, P0 approved scope) ---
    # Evidence-only: does NOT feed dynamic_trade_levels()/entry/SL/T1/T2/
    # RR/trade_score/probability/alert eligibility -- none of those are
    # touched by this block, all still read `premium` (LTP) exactly as
    # before. This exists solely to persist ltp/bid/ask/spread/spread_pct
    # into the centralized evidence log for future statistical
    # validation, per this session's own P0 audit finding that this
    # data was being computed-and-discarded (filters.liquidity_ok())
    # rather than recorded.
    #
    # bid/ask are None when genuinely UNAVAILABLE (both brokers fixed
    # this session to stop defaulting a missing quote to 0.0 -- see
    # upstox.py/angelone.py's own comments). A literal <=0 value (not
    # None) is a THIRD, distinct state -- INVALID, not unavailable and
    # not a genuine zero-width spread -- matching filters.liquidity_ok()'s
    # own <=0 check added alongside this fix.
    #
    # BUGFIX (2026-08-15, P0 audit follow-up): this block used to only
    # check `is not None`, so bid=0.0/ask=0.0 (a real, present, but
    # INVALID value for an option's price) was treated as fully valid
    # and given a computed spread/spread_pct -- inconsistent with
    # liquidity_ok()'s own <=0 rejection for the exact same input.
    # Evidence logging now follows the SAME three-state rule as the
    # gate: VALID (bid>0 and ask>0) -> real spread/spread_pct; anything
    # else -> UNAVAILABLE (None) or INVALID (<=0), spread/spread_pct
    # never fabricated for either. spread_pct uses the midpoint of
    # bid/ask as its base (per this audit's own specified formula) --
    # a DIFFERENT, evidence-focused percentage from filters.
    # liquidity_ok()'s own internal spread_pct (which is
    # (ask-bid)/premium, unchanged, still gates on MAX_SPREAD_PCT
    # exactly as before) -- two related but distinct numbers, not a
    # duplicate of the same one.
    if bid is None or ask is None:
        bid_ask_status = "UNAVAILABLE"
        spread, spread_pct = None, None
    elif bid <= 0 or ask <= 0:
        bid_ask_status = "INVALID"
        spread, spread_pct = None, None
    else:
        bid_ask_status = "VALID"
        spread = ask - bid
        midpoint = (bid + ask) / 2
        spread_pct = (spread / midpoint * 100) if midpoint else None
    _logger.info(
        f"PRICING_EVIDENCE {symbol} {direction} {moneyness} strike={strike} "
        f"ltp={premium} bid={bid if bid is not None else 'UNAVAILABLE'} "
        f"ask={ask if ask is not None else 'UNAVAILABLE'} "
        f"status={bid_ask_status} "
        f"spread={spread if spread is not None else bid_ask_status} "
        f"spread_pct={round(spread_pct, 2) if spread_pct is not None else bid_ask_status}"
    )

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
    #
    # BUGFIX (2026-09-25, shadow-mode feature-correlation audit): this
    # used to be the plain today-only market_data.
    # get_recent_underlying_candles() fetch -- NOT warmup-enhanced, the
    # same class of bug already fixed for ATR (get_atr_warmup_candles())
    # and EMA (get_ema_warmup_candles()) on 2026-08-11/08-20. Confirmed
    # live via 20 real shadow candidates: every single one created
    # before 11:00 IST (18/18) had momentum=None in its SCORE line --
    # indicators.momentum_pct(lookback=6) needs >6 candles, i.e. 7,
    # which today-only 15-min data can't reach until 9:15+105min=11:00
    # IST -- while the 2 candidates created after 11:00 IST both had a
    # real momentum value. Exactly the same 9:15-11:00 IST data-
    # starvation window as the original ATR bug. Now reuses the SAME
    # proven, cached warmup mechanism (dedupes the broker call via the
    # shared _atr_warmup_cache whenever ATR/EMA already warmed this
    # exact (instrument_key, interval) this cycle) instead of a fourth
    # independent, unwarmed fetch.
    today_candles_15m = market_data.get_recent_underlying_candles(underlying_key, "15minute", 30)
    candles_15m, _ = market_data.get_atr_warmup_candles(underlying_key, "15minute", today_candles_15m)
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
    # a SEPARATE candle set, used as input to compute_atr() AND (as of
    # 2026-08-11) compute_ema()/ema_trend_direction() below. candles_15m
    # itself (used for find_nearby_level(), momentum_pct(),
    # efficiency_ratio() just below) is intentionally left completely
    # untouched -- Opening Range, today's High/Low, Session VWAP, and
    # breakout-level logic can never see a previous-session candle
    # through this path. Renamed from `atr_candles` (2026-08-11,
    # "review this diff" pass) -- feeding EMA from a variable named for
    # a different indicator was exactly the kind of naming mismatch
    # that let the EMA-still-uses-candles_15m bug go unnoticed; this
    # name says what it actually is (warmup-aware 15-min candles), not
    # which indicator happened to ask for it first.
    warmup_candles_15m, combined_candle_count = market_data.get_atr_warmup_candles(
        underlying_key, "15minute", candles_15m
    )
    # EMA-specific warmup (2026-08-11) -- see market_data.
    # get_ema_warmup_candles()'s own docstring for why this is a
    # SEPARATE function from get_atr_warmup_candles() above rather than
    # reusing its result directly: that function's cutoff is a fixed
    # wall-clock time (config.ATR_WARMUP_CUTOFF_HOUR/MINUTE, tuned for
    # ATR's shorter 15-candle need) that stops merging warmup candles
    # at 11:00 IST regardless of whether today's own candles have
    # reached EMA21's 21-candle need yet (they haven't -- that's
    # ~14:30 IST) -- using it directly for EMA would trade the original
    # "not ready until 14:30" bug for a new "ready, then unready again
    # at 11:00, then ready again at 14:30" regression. This function's
    # cutoff is DATA-based (merges until today's own count reaches 21
    # on its own) instead of time-based, so it doesn't have that gap.
    # Same reasoning applies to the parallel 5-minute fetch below for
    # m5_direction (21-candle need on 5-min data -> ~11:00 IST
    # today-only).
    warmup_candles_15m_for_ema, _combined_15m_ema_count = market_data.get_ema_warmup_candles(
        underlying_key, "15minute", candles_15m, min_candles=21
    )
    warmup_candles_5m_for_ema, _combined_5m_ema_count = market_data.get_ema_warmup_candles(
        underlying_key, "5minute", candles_5m, min_candles=21
    )

    ema_direction = ema_trend_direction(warmup_candles_15m_for_ema)
    # 5-min EMA direction read (2026-08-10) -- a genuinely SEPARATE
    # computation from ema_direction above: different timeframe,
    # different candle set (warmup-aware 5-min candles, not 15-min) --
    # this pairing is real independent evidence. Feeds ONLY the
    # Mandatory Structural Confirmation check in filters.
    # directional_confirmation_status() -- see that function's
    # docstring for why this exists (trend/vwap alone were satisfying
    # the confirmation-count gate with zero real EMA-structure
    # evidence on either timeframe).
    m5_direction = ema_trend_direction(warmup_candles_5m_for_ema)
    ema_aligned = ema_direction == direction
    ema9 = compute_ema([c["close"] for c in warmup_candles_15m_for_ema], 9)
    ema21 = compute_ema([c["close"] for c in warmup_candles_15m_for_ema], 21)
    vwap_confirmed = vwap_check(symbol, spot_price, direction)
    volume_confirmed = liquidity_ok(oi, volume, bid, ask, premium, symbol)  # see NOTE below
    oi_delta_pct = oi_change_pct(oi_history, instrument_key, oi)  # read BEFORE oi_is_building mutates oi_history
    oi_building = oi_is_building(oi_history, instrument_key, oi)
    liquidity_pass = liquidity_ok(oi, volume, bid, ask, premium, symbol)
    # REMOVED (2026-08-11, "review this diff" pass): a SEPARATE
    # "mtf_confirmed"/HTF signal used to be derived here (first from a
    # redundant, un-warmed re-fetch via option_chain.
    # multi_timeframe_confirmation_detail(), then -- in the immediately
    # preceding fix -- from `ema_direction` directly). Both versions
    # were the SAME underlying 15-min EMA9/21 fact as `ema_direction`
    # above, just presented as if it were independent second evidence.
    # Confirmed live: once both reads were equally fresh, ema_direction
    # and the derived "htf" signal agreed 100% of the time by
    # construction, letting the Directional Confirmation Gate's
    # count>=2 requirement be satisfied by ONE fact counted twice (see
    # filters.directional_confirmation_status()'s docstring for the
    # full trace and reproduction). This codebase has never had a
    # genuine independent higher-timeframe data source (option_chain.
    # multi_timeframe_confirms() computed the identical 15-min
    # ema_trend_direction() even in the original baseline, before this
    # signal-quality gate existed) -- so there is nothing real here to
    # keep under any name. delta_ok/premium_ok below are unrelated,
    # unaffected checks.
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
    atr_index_points = compute_atr(warmup_candles_15m)

    # --- Diagnostics (TEMPORARY -- remove once EMA/ATR-unavailable
    # rejections are confirmed resolved in production; see FIX 1/FIX 2
    # deliverables notes) ---
    _logger.info(
        f"DIAG {symbol} {direction} {moneyness} strike={strike} | "
        f"candles_fetched={len(candles_15m)} combined_candle_count={combined_candle_count} "
        f"ema_ready={ema9 is not None and ema21 is not None} "
        f"atr_ready={atr_index_points is not None}"
    )

    # SPLIT (2026-08-20, root-cause audit item #11): "ATR unavailable
    # or delta==0" used to combine two unrelated failure causes into
    # one ambiguous message -- impossible to tell which one actually
    # fired from the log alone. Now two distinct, structured reason
    # codes (matching this session's own AVAILABLE/UNAVAILABLE/INVALID
    # data-quality vocabulary): CANDLE_HISTORY_INSUFFICIENT (ATR
    # couldn't be computed -- see get_atr_warmup_candles()'s own
    # 2026-08-20 fix for the specific bug this used to mask) vs.
    # DELTA_INVALID (a genuinely separate, unrelated data problem).
    if atr_index_points is None:
        _logger.info(
            f"SKIP {symbol} {direction} {moneyness} strike={strike}: "
            f"CANDLE_HISTORY_INSUFFICIENT (ATR unavailable -- "
            f"candles_fetched={len(candles_15m)} combined_candle_count={combined_candle_count})"
        )
        return None  # dynamic_trade_levels() would raise on this -- bail before it does
    if delta == 0:
        _logger.info(f"SKIP {symbol} {direction} {moneyness} strike={strike}: DELTA_INVALID (delta==0)")
        return None  # dynamic_trade_levels() would raise on this -- bail before it does

    # --- ATR Chase Filter (2026-08-07, config.ATR_CHASE_FILTER_ENABLED) ---
    # UNCHANGED (2026-08-11 structural-extension audit: kept exactly as
    # it was, not replaced) -- rejects a strike when the underlying has
    # already travelled more than ATR_CHASE_MULTIPLIER x today's ATR
    # away from today's session open by the time this cycle runs --
    # buying an option after the move that would have justified it has
    # already happened is exactly the "chasing an extended candle"
    # pattern that tends to stop out. "Today's session open" =
    # candles_15m[0]["open"] -- candles_15m is today-only (see the FIX
    # 2/ATR-warmup comment above: warmup_candles_15m, not candles_15m,
    # is the one that may carry previous-session tail candles), so this
    # is genuinely today's first candle, not yesterday's close.
    #
    # UPDATE (2026-08-11): no longer returns None immediately on its
    # own -- see the Structural Extension Filter and combined
    # EXTENSION_CHECK decision right below, which this now feeds into
    # alongside a second, independently-anchored check. The THRESHOLD
    # and the underlying/session-open math are completely unchanged;
    # only the control flow (compute-then-combine, instead of an
    # immediate early return) is different.
    atr_extended = False
    move_from_open = None
    chase_ceiling = None
    if ATR_CHASE_FILTER_ENABLED:
        session_open = candles_15m[0]["open"]
        move_from_open = abs(spot_price - session_open)
        chase_ceiling = atr_index_points * ATR_CHASE_MULTIPLIER
        atr_extended = move_from_open > chase_ceiling

    # --- Structural Extension Filter (2026-08-11, config.
    # STRUCTURAL_EXTENSION_FILTER_ENABLED) ---
    # SECOND, INDEPENDENT extension check -- see config.
    # STRUCTURAL_EXTENSION_MAX_DISTANCE_ATR's own comment for the full
    # rationale on why this is a differently-anchored question from the
    # ATR Chase Filter above, not a duplicate of it. Reuses
    # `nearby_level` (already computed above, from indicators.
    # find_nearby_level() -- the SAME level RR_LEVEL_CHECK below uses
    # for a DIFFERENT question, "room to target", not this one,
    # "already moved too far past entry" -- see that log line's own
    # comment for why the two stay separate).
    #
    # Direction-aware distance PAST the level: for CE, find_nearby_level()
    # returns the recent HIGH (a resistance/breakout reference) -- spot
    # ABOVE it means price has already broken out; the distance past it
    # is spot_price - nearby_level. For PE, the recent LOW (support/
    # breakdown reference) -- distance past it is nearby_level -
    # spot_price. A NEGATIVE distance (price hasn't reached/broken the
    # level yet) is clamped to 0 -- that's a pre-breakout state, not an
    # extended one, and is never flagged by this check.
    #
    # Conservative by design (requirement: never manufacture a rejection
    # from an unreliable/absent reference) -- find_nearby_level() only
    # returns None when `candles` itself is empty (see that function's
    # own code), which candle-readiness/ATR-availability gates earlier
    # in this same function already make unreachable in practice; the
    # None branch below exists anyway as an explicit, logged no-op
    # rather than an assumption.
    structural_extended = False
    structural_reference_type = None
    structural_distance_points = None
    structural_distance_atr = None
    if STRUCTURAL_EXTENSION_FILTER_ENABLED:
        if nearby_level is None:
            _logger.info(
                f"STRUCTURAL_EXTENSION_CHECK {symbol} direction={direction} "
                f"reference_level=None status=NO_RELIABLE_REFERENCE"
            )
        else:
            structural_reference_type = "recent_high_20c" if direction == "CE" else "recent_low_20c"
            raw_distance = (spot_price - nearby_level) if direction == "CE" else (nearby_level - spot_price)
            structural_distance_points = max(0.0, raw_distance)
            structural_distance_atr = structural_distance_points / atr_index_points
            structural_extended = structural_distance_atr > STRUCTURAL_EXTENSION_MAX_DISTANCE_ATR
            _logger.info(
                f"STRUCTURAL_EXTENSION_CHECK {symbol} direction={direction} "
                f"reference_level={nearby_level} reference_type={structural_reference_type} "
                f"spot={spot_price} distance_points={structural_distance_points:.2f} "
                f"distance_atr={structural_distance_atr:.2f} "
                f"threshold_atr={STRUCTURAL_EXTENSION_MAX_DISTANCE_ATR} "
                f"extended={'Y' if structural_extended else 'N'}"
            )

    # --- Combined Extension Decision (2026-08-11) ---
    # "ATR extension FAIL OR structural extension FAIL -> NO ALERT" --
    # neither check can rescue a rejection from the other; both must
    # pass. This runs before trade score/strengths/probability/Options
    # Intelligence are computed (immediately below) and before
    # detect_setups()'s regime floor / main.py's MIN_ALERT_PROBABILITY,
    # same as the original ATR-only version already was.
    final_extension_pass = not (atr_extended or structural_extended)
    _logger.info(
        f"EXTENSION_CHECK {symbol} {direction} {moneyness} strike={strike} | "
        f"atr_extended={'Y' if atr_extended else 'N'} "
        f"structural_extended={'Y' if structural_extended else 'N'} "
        f"final_extension_pass={'Y' if final_extension_pass else 'N'}"
        + ("" if final_extension_pass else
           " reason=" + "+".join(
               r for r, flag in (("ATR_EXTENSION", atr_extended), ("STRUCTURAL_EXTENSION", structural_extended))
               if flag
           ))
    )
    if not final_extension_pass:
        _rejection_count += 1
        reasons = []
        if atr_extended:
            reasons.append(
                f"Price extended beyond ATR chase ceiling "
                f"(move={move_from_open:.2f}, ceiling={chase_ceiling:.2f} = {ATR_CHASE_MULTIPLIER}x ATR)"
            )
        if structural_extended:
            reasons.append(
                f"Price extended beyond structural reference level "
                f"({structural_reference_type}={nearby_level}, "
                f"distance={structural_distance_atr:.2f}x ATR > {STRUCTURAL_EXTENSION_MAX_DISTANCE_ATR}x)"
            )
        _logger.info(
            f"REJECTED {symbol} {direction} {moneyness} strike={strike} | "
            f"Reasons: {'; '.join(reasons)}"
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
    #
    # SEPARATE FROM (2026-08-11): the Structural Extension Filter above
    # (STRUCTURAL_EXTENSION_CHECK/EXTENSION_CHECK log lines, already run
    # by this point) also uses `nearby_level` -- but asks a THIRD
    # distinct question, "has price already moved too far PAST this
    # level to still be a fresh entry", using a direction-signed
    # distance normalized by ATR (STRUCTURAL_EXTENSION_MAX_DISTANCE_ATR).
    # This RR check below uses an UNSIGNED distance
    # (abs(nearby_level - spot_price)) against a delta-scaled target
    # distance -- "is there room left toward the level", not "how far
    # past it has price already gone". Same input, three genuinely
    # different questions (risk_reward_ok here, risk_reward_strength()
    # in the score, and structural_extended above) -- not the same
    # check three times.
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
    # NEW hard gate, separate from quality_gate()'s risk checks above:
    # requires at least DIRECTIONAL_CONFIRMATION_MIN_COUNT of {EMA(15m)
    # aligned, trend above threshold, momentum above threshold, VWAP
    # confirmed} before a strike proceeds to scoring. Deliberately NOT
    # "all must pass" -- see filters.directional_confirmation_status()'s
    # docstring for why. A separate "HTF" signal used to be counted
    # here too -- removed 2026-08-11 as a duplicate of the EMA(15m)
    # signal, not independent evidence; see that function's docstring
    # for the full trace.
    directional_status = directional_confirmation_status(
        ema_direction, trend_value, signed_momentum,
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
        # REMOVED (2026-08-11, "review this diff" pass): htf_strength()
        # used to be fed a value derived from the same 15-min EMA9/21
        # crossover "ema" above already scores -- double-crediting one
        # fact across two weighted factors (10 + 5 = 15 of 100 points
        # for what was really one signal). This repository has never
        # had a genuine independent higher-timeframe data source (see
        # filters.directional_confirmation_status()'s docstring for the
        # full trace) -- None here lets compute_trade_score()'s
        # existing None-exclusion/renormalization correctly drop this
        # factor's weight instead of inflating the score with a
        # duplicate. TRADE_SCORE_WEIGHTS' "htf" slot is left in place
        # (not deleted) in case a genuine higher-timeframe signal is
        # added later -- it belongs back here as real, independently-
        # sourced evidence, not removed and re-added under a new name.
        "htf": trade_score.htf_strength(None),
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
    # BUGFIX (2026-08-12) -- P0 audit, "probability must not be
    # fabricated": renamed locally from `probability_pct` -- this is
    # trade_score.compute_probability_pct()'s output, a deterministic
    # RESCALING of `score` (see that function's own docstring, unchanged
    # in substance since it was written: "NOT a backtested probability
    # model"). It used to be written straight into
    # candidate["probability_pct"] -- the exact field main.py's
    # MIN_ALERT_PROBABILITY gate and the Telegram alert both treated as
    # a genuine probability. Kept here ONLY as an internal ranking/
    # display heuristic (logged below, distinctly labeled) -- see
    # calibrated_probability_pct further down for the value that
    # actually gates the alert and gets sent to Telegram.
    trade_score_heuristic_pct = trade_score.compute_probability_pct(score)
    heuristic_band = trade_score.probability_label(trade_score_heuristic_pct)
    score_breakdown = trade_score.scoring_reasons(strengths)
    confidence_score = trade_score.compute_confidence_score(score)
    _logger.info(
        f"TRADE_SCORE_HEURISTIC {symbol} {direction} {moneyness} strike={strike} "
        f"heuristic_pct={trade_score_heuristic_pct}% ({heuristic_band}) -- "
        f"a rescaling of trade_score, NOT a calibrated probability; see "
        f"CALIBRATED_PROBABILITY below for the value that actually gates this alert | "
        + " | ".join(score_breakdown)
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
    # PCR parity check, diagnostic-only (2026-08-14, architecture cleanup
    # audit) -- compares this repo's existing, self-computed PCR
    # (chain_metrics.overall_pcr(), already returned above as
    # oi_intel["overall_pcr"]) against Upstox's own dedicated PCR
    # endpoint. Logged only -- oi_intel's actual PCR score/decision is
    # completely UNCHANGED by this; see upstox.UpstoxBroker.
    # get_native_pcr()'s own docstring for why this stays a comparison,
    # not a replacement, until parity is confirmed over real data.
    # Max Pain parity is NOT checked here -- Upstox's own Max Pain
    # endpoint's exact URL/parameters were not found in verified
    # documentation this session, and this repo's own standing rule is
    # to never invent an unverified API shape.
    self_computed_pcr = oi_intel.get("overall_pcr")
    if self_computed_pcr is not None:
        native_pcr = market_data._upstox.get_native_pcr(
            resolve_underlying_key(symbol), expiry, trading_date_str()
        )
        if native_pcr is not None:
            try:
                diff = round(self_computed_pcr - native_pcr, 3)
            except TypeError:
                # Defensive: this endpoint's response typing wasn't
                # confirmed via a live call this session (only its URL/
                # shape from documentation) -- if `pcr` ever comes back
                # as something non-numeric, log that plainly instead of
                # letting an arithmetic error interrupt this strike's scan.
                _logger.info(f"PCR_PARITY_CHECK {symbol} {expiry} self_computed={self_computed_pcr} "
                             f"upstox_native={native_pcr!r} (non-numeric, diff not computed)")
                native_pcr = None
            if native_pcr is not None:
                _logger.info(
                    f"PCR_PARITY_CHECK {symbol} {expiry} self_computed={self_computed_pcr} "
                    f"upstox_native={native_pcr} diff={diff}"
                )
        else:
            _logger.info(f"PCR_PARITY_CHECK {symbol} {expiry} self_computed={self_computed_pcr} upstox_native=UNAVAILABLE")
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

    # --- Calibrated Probability Gate (2026-08-12, P0 audit) ---
    # THE alert-eligibility probability check -- see trade_score.
    # calibrated_probability_pct()'s own docstring for the full
    # rationale and current honest state (this repository has no
    # aggregated, bucketed historical-outcome calibration table yet,
    # so this call always returns None today -- that is the deliberate,
    # safe default, not a bug: "prefer PROBABILITY UNAVAILABLE over a
    # fabricated number" is this system's own stated design principle).
    # `context` intentionally passes everything a real calibration
    # model would plausibly need, even though this stub ignores all of
    # it -- so wiring in a real model later touches only that one
    # function, not this call site.
    calibration_context = {
        "symbol": symbol, "direction": direction, "moneyness": moneyness,
        "expiry_days": expiry_days, "trend_value": trend_value,
        "momentum_value": signed_momentum, "market_regime": market_regime(candles_15m)[0],
        "rr_ratio": rr_ratio, "atr_index_points": atr_index_points,
    }
    calibrated_pct = trade_score.calibrated_probability_pct(calibration_context)
    if calibrated_pct is None:
        _rejection_count += 1
        _logger.info(
            f"CALIBRATED_PROBABILITY {symbol} {direction} {moneyness} strike={strike} status=UNAVAILABLE"
        )
        _logger.info(
            f"REJECTED {symbol} {direction} {moneyness} strike={strike} | "
            f"Reasons: CALIBRATED_PROBABILITY_UNAVAILABLE -- no alert without a "
            f"genuine, evidence-based probability estimate (trade_score={score}/100, "
            f"heuristic_pct={trade_score_heuristic_pct}% -- neither is a substitute)"
        )
        if PROBABILITY_SHADOW_MODE_ENABLED:
            # Phase-2 Shadow/Validation Mode (2026-08-23): this candidate
            # already cleared EVERY hard gate above (liquidity, RR, OI,
            # VWAP, trend, extension, contract validity, Options
            # Intelligence) -- the ONLY reason it's not a live alert is
            # the still-empty calibration model. Captured here for
            # outcome tracking so resolved-trade data can eventually
            # accumulate toward a real model -- see main.py's
            # create_shadow_candidates() for what happens to this dict
            # next. probability_pct/probability_label are explicitly
            # None (never heuristic_pct -- that would be exactly the
            # fabrication this system's own principle forbids), and
            # execution_mode="SHADOW" is the one field every downstream
            # consumer (main.py, telegram_notify.py) must check before
            # ever treating this as alertable.
            shadow_candidate = {
                "symbol": symbol, "direction": direction, "moneyness": moneyness,
                "instrument_key": instrument_key, "strike": strike, "expiry": expiry,
                "spot_price": spot_price, "alert_premium": premium,
                "entry_low": levels["entry_low"], "entry_high": levels["entry_high"],
                "stop_loss": levels["stop_loss"], "target_1": levels["target_1"],
                "target_2": levels["target_2"], "grade": grade,
                "trade_score": score, "probability_pct": None, "probability_label": None,
                "score_breakdown": score_breakdown, "confidence_score": confidence_score,
                "risk_score": risk_score, "reward_score": reward_score, "expected_rr": expected_rr,
                "factor_checklist": trade_score.factor_checklist(strengths),
                "confidence_stars": stars, "confidence_label": confidence_label,
                "risk_rating": risk_emoji, "risk_label": risk_label,
                "expected_holding_minutes": holding_minutes,
                "why_reasons": reasons_from_factors(factors), "session": session,
                "alternatives": [],
                "probability_status": "UNAVAILABLE",
                "execution_mode": "SHADOW",
                "live_alert_sent": False,
                "candidate_id": f"{symbol}:{direction}:{strike}:{expiry}",
            }
            _shadow_candidates_this_cycle.append(shadow_candidate)
            _logger.info(
                f"SHADOW_CANDIDATE_CREATED {symbol} {direction} {moneyness} strike={strike} "
                f"candidate_id={shadow_candidate['candidate_id']} trade_score={score}/100"
            )
        return None
    probability_pct = calibrated_pct
    probability_band = trade_score.probability_label(probability_pct)
    _logger.info(
        f"CALIBRATED_PROBABILITY {symbol} {direction} {moneyness} strike={strike} "
        f"probability={probability_pct}% ({probability_band}) status=AVAILABLE"
    )

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
        # BUGFIX (2026-09-25, same audit as above): market_regime()
        # calls compute_atr() internally (needs 15 candles) -- this was
        # also feeding it the plain today-only fetch, so before ~12:45
        # IST it would silently fall through to the "SIDEWAYS" default
        # purely from insufficient data, not a genuine sideways read,
        # skewing min_quality_score's regime penalty for the whole
        # early session. Same warmup reuse as candles_15m above.
        today_candles_15m_regime = market_data.get_recent_underlying_candles(underlying_key, "15minute", 30)
        candles_15m, _ = market_data.get_atr_warmup_candles(underlying_key, "15minute", today_candles_15m_regime)
        regime, penalty = market_regime(candles_15m)
        min_quality_score += penalty

    biases, trend_status = directional_bias(symbol)
    # Diagnostics (TEMPORARY -- see FIX 1/FIX 2 deliverables notes).
    # BUGFIX (2026-08-20): "bias_ready=True" used to be logged even in
    # the UNKNOWN/CE+PE fallback case (directional_bias() always
    # returns a non-empty list except when the instrument itself can't
    # be resolved) -- meaning this line could never actually prove real
    # EMA data existed. trend_status is now logged explicitly alongside
    # it (CONFIRMED/CONFLICTED/UNKNOWN, see directional_bias()'s own
    # docstring) so "ready" and "confirmed" are never conflated again.
    _logger.info(f"DIAG {symbol}: bias_ready={bool(biases)} trend_status={trend_status}")
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
