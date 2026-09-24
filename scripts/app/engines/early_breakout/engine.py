"""
app/engines/early_breakout/engine.py -- Early Breakout Engine entry point.
------------------------------------------------------------------------------
Upgraded 2026-07-24 into the five-stage "Institutional Accumulation"
pipeline, IN PLACE -- this replaced the original four-stage version
rather than sitting alongside it as a third engine (see this session's
summary for why: avoiding a third parallel pass over every symbol,
tripling Angel One load, and a third independent Telegram format).

  Stage 1 -- Underlying First           underlying trend/momentum/VWAP/
                                         EMA read, BEFORE any option is
                                         selected (see _score_symbol_direction()
                                         below -- the ATM strike/leg is
                                         only resolved after Stages 1-3
                                         all pass)
  Stage 2 -- Institutional Accumulation app/engines/early_breakout/institutional_score.py,
                                         gated at >=70 (config.INSTITUTIONAL_SCORE_MIN)
  Stage 3 -- Base Formation             app/engines/early_breakout/base_detector.py (now
                                         includes the chart-pattern bonus),
                                         gated at >=70 (config.BASE_SCORE_MIN)
  Stage 4 -- Early Breakout             app/engines/early_breakout/breakout_detector.py,
                                         searches backward for the FIRST
                                         breakout candle and its "age"
  Stage 5 -- One-Candle Confirmation    app/engines/early_breakout/breakout_detector.py,
                                         exactly the candle right after
                                         the breakout candle

Runs ALONGSIDE scanner.py's existing Trade Score Engine, not instead of
it -- see main.run_early_breakout_step(). Produces trade dicts with the
same keys main.new_trade_record() already expects, plus this engine's
own extra fields (early_breakout_reasons, base_score, institutional_score,
momentum_score, smart_money_score, breakout_price, entry_zone,
maximum_entry, fvg_confirmed) that telegram_notify.fmt_early_breakout_alert()
reads. Every existing consumer (tracker.py, state.py) only reads keys it
already knows, so these extra fields are inert to them.

FAIR VALUE GAP (2026-07-25): fair_value_gap.py adds a non-gating
confirmation check (config.FVG_CONFIRMATION_BONUS) between Stage 5 and
scoring -- an unfilled 3-candle imbalance gap in the trade's direction,
formed near the breakout, adds a small score bonus. This was the ONE
sub-engine built from a much larger "Institutional Intelligence Engine"
request (Order Blocks, Break of Structure/CHoCH, Volume Profile,
Liquidity Sweeps, Supply/Demand zones, Order Flow, Delivery Analysis,
and more). It was picked because: (a) Institutional Score and Smart
Money Score already exist here (institutional_score.py,
smart_money_score.py) and re-asking for them would duplicate, not add,
(b) Order Flow / Delta Volume / Delivery Analysis need L2 tick data or
delivery percentages neither Angel One nor Upstox's option-chain
response provides (same honesty constraint documented in
smart_money_score.py), and (c) FVG has the single crispest, least
ambiguous definition of everything requested -- lower risk of a subtly
wrong implementation than order blocks/BOS-CHoCH, which have several
informal, competing definitions depending on the source. The other
sub-engines were deliberately NOT built in this pass rather than
generated shallow.

PRICE ACTION (2026-07-26): price_action.py adds the same kind of
non-gating confirmation bonus (config.PRICE_ACTION_CONFIRMATION_BONUS)
for standard candlestick patterns (Doji, Hammer, Shooting Star,
Marubozu, Bullish/Bearish Engulfing, Inside/Outside Bar, Momentum
Candle) on the breakout/confirmation candles, aligned with the trade's
direction. Picked from a similarly large "Trading Intelligence Engine"
request (Part 4: Multi-Timeframe, Trend, Breakout, Breakdown, Base
Formation, full Pattern Recognition, Price Action, S/R Validation,
Momentum/Volume Confirmation, Entry Quality, Signal Validation, Trading
Score/Filter -- most of which already exist elsewhere in this codebase
under different names, or need new broker calls this pass avoids) --
picked because it was asked for TWICE (Part 3 and Part 4) and, like
FVG, has textbook-standard geometric definitions with little room for
ambiguity, unlike full chart patterns (Head & Shoulders, Cup & Handle,
Double Top/Bottom), which need multi-swing-point detection and were
NOT built here for the same "higher risk of getting it subtly wrong"
reason order blocks/BOS-CHoCH weren't.
"""

import market_data
from logging_setup import get_logger
from config import (
    SYMBOLS, BASE_SCORE_MIN, BASE_LOOKBACK_CANDLES, EARLY_BREAKOUT_MIN_RR,
    INSTITUTIONAL_SCORE_MIN, EARLY_ENTRY_PREFERRED_MIN_PCT, EARLY_ENTRY_PREFERRED_MAX_PCT,
    EARLY_ENTRY_MAX_PCT, EARLY_BREAKOUT_CANDLE_FETCH_COUNT,
)
from indicators import compute_ema, compute_atr, compute_vwap
from app.market import snapshot as market_snapshot
from option_chain import resolve_underlying_key, get_atm_strike, current_expiry, _extract_leg
from filters import liquidity_ok, oi_change_pct, oi_is_building
from levels import dynamic_trade_levels, estimated_holding_minutes
from scanner import current_session
import trade_score

from . import base_detector, breakout_detector, breakout_score, momentum, fair_value_gap, price_action
from . import institutional_score as institutional_score_mod
from . import smart_money_score as smart_money_score_mod

_logger = get_logger("early_breakout")

_last_scan_stats = {"candidates": 0, "rejected": 0}


def _confidence_stars(score: int) -> str:
    filled = max(1, min(5, round(score / 20)))
    return "★" * filled + "☆" * (5 - filled)


def _entry_window(breakout_price: float) -> dict:
    """
    Early Entry Window (spec example: breakout ₹200 -> preferred
    ₹202-214, maximum ₹220, reject above ₹220). Percent offsets are
    config-driven (EARLY_ENTRY_PREFERRED_MIN/MAX_PCT, EARLY_ENTRY_MAX_PCT)
    so they can be retuned without touching this function.
    """
    return {
        "preferred_low": round(breakout_price * (1 + EARLY_ENTRY_PREFERRED_MIN_PCT), 2),
        "preferred_high": round(breakout_price * (1 + EARLY_ENTRY_PREFERRED_MAX_PCT), 2),
        "maximum_entry": round(breakout_price * (1 + EARLY_ENTRY_MAX_PCT), 2),
    }


def _score_symbol_direction(symbol: str, direction: str, session: str, oi_history: dict):
    underlying_key = resolve_underlying_key(symbol)
    if not underlying_key:
        return None

    # --- Stage 1: Underlying First -- fetched and read BEFORE any
    # option/strike is selected below. get_atm_strike() is the only
    # option-adjacent call this early, and it only reads the spot price
    # (needed to compute ATR-relative distances) -- no strike/leg is
    # resolved yet.
    # BUGFIX (2026-09-25, same audit as scanner.py's candles_15m/
    # momentum fix): this used to be the plain today-only fetch --
    # with BASE_LOOKBACK_CANDLES=20 (the len() gate just below needing
    # 22), today-only 15-min data can't reach that until
    # 9:15+22*15min=~14:45 IST -- meaning this entire engine was
    # effectively dormant for all but the last ~45 minutes of every
    # trading day, not gated by any disabled flag (run_early_breakout_
    # step() runs unconditionally every cycle). Same warmup reuse as
    # scanner.py's fix -- ema50 below (needing 50 candles, never
    # reachable from a single day's ~25 15-min candles even by close)
    # benefits further still from get_market_snapshot()'s own EMA50
    # read when available, but the LOCAL fallback here (used whenever
    # that snapshot can't be built) is what this fixes directly.
    today_candles = market_data.get_recent_underlying_candles(underlying_key, "15minute", EARLY_BREAKOUT_CANDLE_FETCH_COUNT)
    candles, _ = market_data.get_atr_warmup_candles(underlying_key, "15minute", today_candles, min_candles=EARLY_BREAKOUT_CANDLE_FETCH_COUNT)
    if len(candles) < BASE_LOOKBACK_CANDLES + 2:
        return None  # genuine data-absence case, not a signal-quality question

    closes = [c["close"] for c in candles]
    atm_strike, spot_price = get_atm_strike(symbol)

    # Market Snapshot (app/market/snapshot.py, added 2026-07-25): computed
    # once per symbol per run and cached there, so this reuses the SAME
    # EMA9/20/21/50/ATR/VWAP reading instead of recomputing it a second
    # time for the same candles -- this call site is also what fixed the
    # ema50-always-None bug above, since the snapshot fetches enough
    # candles for a genuine EMA50. Falls back to computing locally only
    # if the snapshot can't be built at all (e.g. underlying_key changed
    # between calls, which shouldn't happen within one run, but this
    # keeps the engine working rather than raising if it ever does).
    snapshot = market_snapshot.get_market_snapshot(symbol)
    if snapshot:
        ema9, ema20, ema21, ema50 = snapshot["ema9"], snapshot["ema20"], snapshot["ema21"], snapshot["ema50"]
        atr = snapshot["atr"]
        vwap = snapshot["vwap"]
    else:
        vwap = compute_vwap(candles[-20:])
        ema9 = compute_ema(closes, 9)
        ema21 = compute_ema(closes, 21)
        ema50 = compute_ema(closes, 50)
        ema20 = compute_ema(closes, 20)
        atr = compute_atr(candles)

    underlying_trend = breakout_score.trend_strength(ema9, ema21, ema50, direction)

    # --- Stage 2: Institutional Accumulation ---
    base_window = candles[:-2]
    inst = institutional_score_mod.compute_institutional_score(base_window, vwap)
    if inst["institutional_score"] < INSTITUTIONAL_SCORE_MIN:
        return None

    # --- Stage 3: Base Formation (incl. chart-pattern bonus) ---
    base = base_detector.compute_base_score(base_window, spot_price, vwap, atr)
    if base["base_score"] < BASE_SCORE_MIN:
        return None

    # --- Stage 4: Early Breakout (backward-searching, ages the result) ---
    breakout = breakout_detector.detect_early_breakout(candles, direction, vwap, ema9, ema21, ema50)
    if not breakout["breakout"]:
        return None

    # --- Stage 5: One-Candle Confirmation -- the candle immediately
    # after the breakout candle, wherever that landed (age 0..3).
    offset = breakout["breakout_age"] + 2
    confirm_candle = candles[-(offset - 1)]
    follow_through = breakout_detector.confirm_follow_through(confirm_candle, direction, breakout["breakout_candle"])
    if not follow_through["confirmed"]:
        return None

    # --- Option selected ONLY NOW, after Stages 1-5 all confirm on the underlying ---
    expiry = current_expiry(symbol)
    chain = market_data.get_option_chain(underlying_key, expiry)
    leg = _extract_leg(chain, atm_strike, direction)
    if leg is None:
        return None

    instrument_key = leg["instrument_key"]
    premium, oi, volume = leg["ltp"], leg["oi"], leg["volume"]
    bid, ask, delta = leg["bid"], leg["ask"], leg["delta"]
    if premium is None or delta is None or delta == 0 or not atr:
        return None

    # Fix 10 (carried over): liquidity is a HARD filter, never softened.
    if not liquidity_ok(oi, volume, bid, ask, premium, symbol):
        _last_scan_stats["rejected"] += 1
        _logger.info(f"EB_REJECTED {symbol} {direction}: liquidity gate failed")
        return None

    # --- Early Entry Window ---
    breakout_price = breakout["breakout_candle"]["close"]
    entry_window = _entry_window(breakout_price)
    if premium > entry_window["maximum_entry"]:
        _last_scan_stats["rejected"] += 1
        _logger.info(
            f"EB_REJECTED {symbol} {direction}: premium {premium} above the "
            f"maximum entry {entry_window['maximum_entry']} (breakout {breakout_price}) -- never chase"
        )
        return None

    # --- Extended Move filter (tightened + parabolic check) ---
    extended = breakout_detector.extended_move_filter(candles, direction, breakout_price, premium, ema20)
    if extended["extended"]:
        _last_scan_stats["rejected"] += 1
        _logger.info(f"EB_REJECTED {symbol} {direction}: {', '.join(extended['reasons'])}")
        return None

    # BUGFIX (2026-07-24, live pattern -- see levels.dynamic_trade_levels()'s
    # MIN_LEVEL_DISTANCE_ATR_MULT note): dynamic_trade_levels() expects
    # a level genuinely AHEAD of price (resistance for CE, support for
    # PE) to use as a target cap -- this used to pass consolidation_low
    # for CE, which is BEHIND/below price (the base we just broke out
    # of), backwards from what the function expects. Even the correct
    # side (consolidation_high for CE) is already-broken resistance at
    # this point, not a genuine level still ahead of a fresh breakout --
    # this engine's base-window data doesn't give us a validated "next"
    # level the way scanner.py's find_nearby_level() does over a longer
    # underlying history. Passing None here (no cap) is more honest than
    # fabricating one; the raw ATR-based T1/T2 apply uncapped.
    nearby_level = None
    levels = dynamic_trade_levels(premium, atr, delta, direction, spot_price, nearby_level)
    target_distance = levels["target_1"] - premium
    sl_distance = premium - levels["stop_loss"]
    rr_ratio = (target_distance / sl_distance) if sl_distance > 0 else 0.0
    if rr_ratio < EARLY_BREAKOUT_MIN_RR:
        _last_scan_stats["rejected"] += 1
        _logger.info(f"EB_REJECTED {symbol} {direction}: RR {rr_ratio:.2f} below minimum {EARLY_BREAKOUT_MIN_RR}")
        return None

    mom = momentum.momentum_strength(candles, direction, vwap, spot_price)

    oi_delta_pct = oi_change_pct(oi_history, instrument_key, oi)
    oi_is_building(oi_history, instrument_key, oi)  # advance shared history, same call scanner.py's engine makes
    bc = breakout["breakout_candle"]
    price_favorable = (bc["close"] > bc["open"]) if direction == "CE" else (bc["close"] < bc["open"])
    oi_state = breakout_score.classify_oi_state(oi_delta_pct, price_favorable)

    # --- Smart Money Score -- only computable now that the leg is known ---
    avg_leg_volume = breakout["avg_volume"]  # underlying-candle avg volume is NOT the option's; see below
    smart_money = smart_money_score_mod.compute_smart_money_score(
        volume=volume, avg_volume=volume,  # no historical option-volume series is fetched elsewhere in
        # this codebase either (scanner.py's engine doesn't track it
        # per-instrument across scans) -- using the single latest
        # reading against itself makes _volume_spike_score() correctly
        # return 0 (no spike detectable from one sample) rather than a
        # misleading number computed against the UNDERLYING's volume,
        # which is a different instrument on a different scale.
        oi_change_pct_value=oi_delta_pct, delta=delta, bid=bid, ask=ask, premium=premium,
    )

    ft_volume = breakout_score.follow_through_strength(follow_through, confirm_candle.get("volume"), bc.get("volume"))

    # --- Fair Value Gap confirmation (2026-07-25 pilot addition -- see
    # fair_value_gap.py's module docstring) -- non-gating, same
    # "confirmation bonus, not a rejection reason" pattern as the OI
    # bonus above: an unfilled FVG in the trade's own direction, formed
    # near the breakout, adds a small score bonus and a Telegram
    # reason line; its absence never rejects an otherwise-qualifying setup.
    fvg = fair_value_gap.fvg_confirmation(candles, direction, atr, breakout["breakout_age"])
    pa = price_action.price_action_confirmation(candles, direction, atr)

    strengths = {
        "underlying_trend": underlying_trend,
        "institutional": breakout_score.institutional_strength(inst["institutional_score"]),
        "base": base["base_score"] / 100,
        "breakout": breakout_score.breakout_strength(breakout),
        "follow_through": ft_volume,
        "volume": breakout_score.volume_strength(bc.get("volume") or 0, breakout["avg_volume"]),
        "momentum": mom["score"],
        "liquidity": trade_score.liquidity_strength(oi, volume, bid, ask, premium, symbol),
        "risk_reward": breakout_score.risk_reward_strength(rr_ratio),
        "smart_money": breakout_score.smart_money_strength(smart_money["smart_money_score"]),
    }
    score = breakout_score.total_score(
        strengths, oi_state, fresh_score=breakout["fresh_score"],
        fvg_confirmed=fvg["confirmed"], price_action_confirmed=pa["confirmed"],
    )
    grade = breakout_score.compute_grade(score)
    if grade == "Reject":
        _last_scan_stats["rejected"] += 1
        _logger.info(
            f"EB_REJECTED {symbol} {direction}: score {score}/100 below Watchlist floor "
            f"(breakout age {breakout['breakout_age']}, fresh multiplier {breakout['fresh_score']}%)"
        )
        return None

    probability_pct = breakout_score.compute_probability_pct(score)
    holding_minutes = estimated_holding_minutes(atr, abs(target_distance / delta) if delta else 0)
    level_txt = f"{breakout['consolidation_high']:.1f}" if direction == "CE" else f"{breakout['consolidation_low']:.1f}"
    invalidation_txt = f"{breakout['consolidation_low']:.1f}" if direction == "CE" else f"{breakout['consolidation_high']:.1f}"

    reasons = {
        "trend": f"Underlying EMA9/21/50 aligned {'bullish' if direction == 'CE' else 'bearish'}",
        "vwap": f"Price {'above' if direction == 'CE' else 'below'} VWAP",
        "institutional": f"Institutional Score {inst['institutional_score']}/100",
        "base": f"Base Score {base['base_score']}/100" + (f" ({base['pattern_label']})" if base.get("pattern_label") else ""),
        "breakout": (f"Closed {'above' if direction == 'CE' else 'below'} consolidation ({level_txt}), "
                     f"age {breakout['breakout_age']} candle(s) -- {breakout['fresh_score']}% freshness"),
        "follow_through": "Confirmed on the next candle (structure + volume)",
        "volume": (f"{(bc.get('volume') or 0) / breakout['avg_volume']:.1f}x base-window average"
                   if breakout["avg_volume"] else "n/a"),
        "liquidity": (
            "Comfortable" if strengths["liquidity"] is not None and strengths["liquidity"] >= 0.5
            else "Unavailable/invalid quote data" if strengths["liquidity"] is None
            else "Thin, but passing the hard filter"
        ),
        "momentum": f"{mom['score'] * 100:.0f}/100 (ADX/ROC/RSI-slope/MACD-hist/EMA-slope/vol-accel/body%/VWAP-dist)",
        "smart_money": f"Smart Money Score {smart_money['smart_money_score']}/100",
        "fair_value_gap": (
            f"Unfilled {direction} gap confirms ({fvg['gap']['gap_low']:.1f}-{fvg['gap']['gap_high']:.1f})"
            if fvg["confirmed"] else "No unfilled gap in this direction yet"
        ),
        "price_action": (
            f"Confirmed by {', '.join(p.replace('_', ' ').title() for p in pa['patterns'])}"
            if pa["confirmed"] else "No aligned candlestick pattern"
        ),
        "invalidation": f"Close back {'below' if direction == 'CE' else 'above'} {invalidation_txt}",
        "market_context": (
            f"{snapshot['regime'].replace('_', ' ').title()}, {snapshot['trend_strength_label']} trend, "
            f"{snapshot['volatility_level']} volatility"
            + (f", Gap {snapshot['gap']['direction']} {abs(snapshot['gap']['gap_pct']):.1%}" if snapshot["gap"]["direction"] else "")
        ) if snapshot else "Market Snapshot unavailable this run",
    }
    why_reasons = [f"✓ {v}" for v in reasons.values()]

    return {
        "strategy": "early_breakout",
        "symbol": symbol, "direction": direction, "moneyness": "ATM",
        "instrument_key": instrument_key, "strike": atm_strike, "expiry": expiry,
        "spot_price": spot_price, "alert_premium": premium,
        "entry_low": levels["entry_low"], "entry_high": levels["entry_high"],
        "stop_loss": levels["stop_loss"], "target_1": levels["target_1"], "target_2": levels["target_2"],
        "grade": grade, "trade_score": score,
        "probability_pct": probability_pct, "probability_label": grade,
        # FIX (2026-07-31): same None-unsafe :.2f formatting bug found
        # and confirmed crashing live in scanner.py's SCORE log line --
        # applied the same defensive fix here since strengths.items()
        # can carry the same "genuinely unavailable this cycle" None
        # values. Hadn't been observed crashing here yet, but the
        # pattern was identical.
        "score_breakdown": [
            f"{k.replace('_', ' ').title()}: {v:.2f}" if v is not None
            else f"{k.replace('_', ' ').title()}: None"
            for k, v in strengths.items()
        ],
        "confidence_score": round(score / 10, 1),
        "risk_score": round(min(10.0, rr_ratio * 3), 1),
        "reward_score": round(min(10.0, rr_ratio * 4), 1),
        "expected_rr": f"1:{rr_ratio:.1f}",
        "factor_checklist": why_reasons,
        "confidence_stars": _confidence_stars(score),
        "confidence_label": grade,
        "risk_rating": "🟢" if rr_ratio >= EARLY_BREAKOUT_MIN_RR * 1.25 else "🟡",
        "risk_label": "Low" if rr_ratio >= EARLY_BREAKOUT_MIN_RR * 1.25 else "Medium",
        "expected_holding_minutes": holding_minutes,
        "why_reasons": why_reasons,
        "session": session, "alternatives": [],
        "early_breakout_reasons": reasons,
        "base_score": base["base_score"],
        "institutional_score": inst["institutional_score"],
        "momentum_score": round(mom["score"] * 100),
        "smart_money_score": smart_money["smart_money_score"],
        "breakout_price": round(breakout_price, 2),
        "entry_zone": f"₹{entry_window['preferred_low']} - ₹{entry_window['preferred_high']}",
        "maximum_entry": entry_window["maximum_entry"],
        "fvg_confirmed": fvg["confirmed"],
        "price_action_confirmed": pa["confirmed"],
    }


def detect_all_early_breakout_setups(oi_history: dict) -> list:
    """
    Top-level entry point, called once per scanner cycle from
    main.run_early_breakout_step(). Returns setups sorted best-first;
    caller decides how many to actually open as trades (concurrent-
    trade-limit / cooldown / suppression all still apply, same as the
    existing engine).
    """
    session = current_session()
    if session is None:
        return []

    _last_scan_stats["candidates"] = 0
    _last_scan_stats["rejected"] = 0

    candidates = []
    for symbol in SYMBOLS:
        for direction in ("CE", "PE"):
            try:
                setup = _score_symbol_direction(symbol, direction, session, oi_history)
            except Exception as exc:
                _logger.error(
                    f"EB_SYMBOL_FAILED {symbol} {direction}: {type(exc).__name__}: {exc}",
                    exc_info=True,
                )
                continue
            if setup:
                candidates.append(setup)

    candidates.sort(key=lambda c: c["trade_score"], reverse=True)
    _last_scan_stats["candidates"] = len(candidates)
    return candidates


def get_last_scan_stats() -> dict:
    return dict(_last_scan_stats)
