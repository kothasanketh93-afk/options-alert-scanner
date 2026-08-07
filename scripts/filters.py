"""
filters.py -- Trade Quality Gate factor checks, split out of scanner.py.
--------------------------------------------------------------------------
Individual pass/fail checks (liquidity, OI build, delta/theta floors)
plus the gate itself: ALL required factors must pass or the setup is
rejected outright -- no partial-credit alert.
"""

from config import (
    MIN_OI, MIN_OPTION_VOLUME, MIN_PREMIUM, MAX_SPREAD_PCT,
    OI_BUILD_THRESHOLD_PCT, MIN_DELTA_OTM,
    EXPIRY_DAY_MIN_OTM_DELTA, EXPIRY_DAY_THRESHOLD_DAYS,
    STOCK_SYMBOLS, STOCK_MIN_OI, STOCK_MIN_OPTION_VOLUME, STOCK_MIN_PREMIUM,
    CANDLE_READINESS_ENABLED, CANDLE_READINESS_MIN_5M, CANDLE_READINESS_MIN_15M,
    PREMIUM_RANGE_ENABLED, PREMIUM_RANGE,
    DIRECTIONAL_CONFIRMATION_ENABLED, DIRECTIONAL_CONFIRMATION_MIN_COUNT,
    EFFICIENCY_TRENDING_THRESHOLD, MOMENTUM_CONFIRM_THRESHOLD_PCT,
)
from option_chain import days_to_expiry


def oi_change_pct(oi_history: dict, instrument_key: str, current_oi: float) -> float:
    """
    Phase 3 (trade_score.py): read-only signed % OI change since the
    last reading -- same lookup oi_is_building() does internally, but
    without mutating oi_history, so callers can get the CONTINUOUS
    value for trade_score.oi_build_strength() and still call
    oi_is_building() afterward for the existing binary gate/reasons
    factor without double-writing history. Call this BEFORE
    oi_is_building() for the same instrument_key in the same run.
    """
    previous = oi_history.get(instrument_key)
    if previous is None or previous <= 0:
        return 0.0
    return (current_oi - previous) / previous


def oi_is_building(oi_history: dict, instrument_key: str, current_oi: float) -> bool:
    previous = oi_history.get(instrument_key)
    oi_history[instrument_key] = current_oi
    if previous is None or previous <= 0:
        return False
    return (current_oi - previous) / previous >= OI_BUILD_THRESHOLD_PCT


def liquidity_ok(oi: float, volume: float, bid: float, ask: float, premium: float,
                  symbol: str = None) -> bool:
    """
    symbol is optional (defaults to the index thresholds below for
    backward compatibility) -- pass it for stock symbols so
    STOCK_MIN_OI/STOCK_MIN_OPTION_VOLUME/STOCK_MIN_PREMIUM apply
    instead of the NIFTY/BANKNIFTY-tuned globals, which are far too
    strict for mid/smallcap stock options and would reject nearly
    every stock strike outright otherwise.
    """
    is_stock = symbol in STOCK_SYMBOLS
    min_oi = STOCK_MIN_OI if is_stock else MIN_OI
    min_volume = STOCK_MIN_OPTION_VOLUME if is_stock else MIN_OPTION_VOLUME
    min_premium = STOCK_MIN_PREMIUM if is_stock else MIN_PREMIUM

    if oi < min_oi:
        return False
    if volume < min_volume:
        return False
    if premium < min_premium:
        return False
    spread_pct = (ask - bid) / premium
    if spread_pct > MAX_SPREAD_PCT:
        return False
    return True


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


def expiry_theta_ok(moneyness: str, delta: float, expiry_date_str: str, now=None) -> bool:
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


def candles_ready(candles_5m_count: int, candles_15m_count: int) -> bool:
    """
    NEW hard gate (2026-08-07): a strike is only eligible for scoring
    once EITHER timeframe has enough COMPLETED candles for its own
    indicators to mean something -- not "some data", literally enough
    candles for the specific math each factor does (momentum_pct()'s
    default 6-candle lookback needs >=7 15-min candles before it ever
    returns non-None; the 5-min OR-path exists so a symbol isn't
    starved of any signal for the ~105 minutes 15-min data alone would
    take to reach that same depth).

    This does NOT guarantee every factor is ready (EMA9/21 still needs
    21 candles on whichever timeframe is used for it) -- it only stops
    the degenerate candles_fetched=1 case documented in the SCORE/GATE
    logs, where trend/momentum/market_strength/htf all read as hard
    zero-not-unknown and liquidity/volume/OI alone can still clear
    ENTRY_SCORE. See indicators.efficiency_ratio()'s and
    trade_score.py's factor functions for the complementary "None
    means unavailable" fix -- this gate and that fix address the same
    root problem from two different angles (stop scoring too early /
    don't lie about zero when scoring does happen anyway).
    """
    if not CANDLE_READINESS_ENABLED:
        return True
    return (
        candles_5m_count >= CANDLE_READINESS_MIN_5M
        or candles_15m_count >= CANDLE_READINESS_MIN_15M
    )


def premium_in_range(symbol: str, premium: float) -> bool:
    """
    NEW hard gate (2026-08-07): rejects option premiums outside a
    per-symbol (min, max) band -- too cheap usually means deep OTM/
    near-worthless time value (the exact "Poor Premium Quality" shape
    options_intelligence.py already flags downstream, just earlier and
    for free); too expensive usually means deep ITM, where the
    position behaves like the underlying and barely uses leverage.

    Only symbols present in config.PREMIUM_RANGE are checked -- any
    symbol not listed there passes through unchanged (existing
    MIN_PREMIUM/STOCK_MIN_PREMIUM lower-bound checks inside
    liquidity_ok() keep applying to it exactly as before). This is
    deliberate: defaulting every symbol into a band tuned from only
    two examples (NIFTY/BANKNIFTY) would misfire for SENSEX/
    MIDCPNIFTY/stocks with very different premium scales.
    """
    if not PREMIUM_RANGE_ENABLED:
        return True
    band = PREMIUM_RANGE.get(symbol)
    if band is None:
        return True  # symbol not configured -- not filtered by this gate
    low, high = band
    return low <= premium <= high


def directional_confirmation_status(
    ema_direction, mtf_confirmed, trend_value, momentum_value, vwap_confirmed_flag, direction: str,
) -> dict:
    """
    NEW hard gate input (2026-08-07): counts how many of 5 directional
    signals actually CONFIRM this trade's direction, and requires at
    least DIRECTIONAL_CONFIRMATION_MIN_COUNT of them before a strike
    is scored at all. Independent of quality_gate()'s 3 risk gates
    (liquidity/delta/theta) -- this is a signal-quality gate, kept
    separate so each can be tuned/disabled on its own.

    Deliberately NOT "all 5 must pass" -- filters.quality_gate()'s own
    docstring already documents why an unrealistic AND across several
    semi-independent signals never once passed live (26 consecutive
    scan cycles, 2026-07-17). DIRECTIONAL_CONFIRMATION_MIN_COUNT=2
    (default) asks for corroboration, not unanimity.

    Every input here is TRI-STATE aware (True/False/None) so a
    genuinely-not-ready signal (e.g. EMA9/21 not yet computable, or
    trend/momentum needing candles that don't exist yet) is reported
    as "Not Ready"/"Insufficient candles"/"Unavailable" rather than
    silently counting as "confirmed against the trade" the way a bare
    boolean would -- this is the same "None != False" distinction
    indicators.py/trade_score.py apply elsewhere, extended to gate
    logging so REJECTED lines say WHY a signal didn't count, not just
    that it didn't.

    ema_direction: "CE"/"PE"/None (the RAW ema_trend_direction() read,
        not the already-collapsed ema_aligned bool -- so "not ready"
        and "confirmed opposite" can be told apart here).
    mtf_confirmed: True/False/None (option_chain.
        multi_timeframe_confirmation_detail()'s tri-state result).
    trend_value: efficiency_ratio() reading (float or None).
    momentum_value: signed momentum_pct() reading, already applied to
        `direction` (float or None) -- positive means aligned.
    vwap_confirmed_flag: bool (vwap_check() -- already fails closed to
        False on missing data, no tri-state available at that layer).
    """
    detail = {}
    count = 0

    if ema_direction is None:
        detail["ema"] = "Not Ready"
    elif ema_direction == direction:
        detail["ema"] = "Confirmed"
        count += 1
    else:
        detail["ema"] = "Not Confirmed"

    if mtf_confirmed is None:
        detail["htf"] = "Not Ready"
    elif mtf_confirmed:
        detail["htf"] = "Confirmed"
        count += 1
    else:
        detail["htf"] = "Not Confirmed"

    if trend_value is None:
        detail["trend"] = "Insufficient candles"
    elif trend_value >= EFFICIENCY_TRENDING_THRESHOLD:
        detail["trend"] = "Confirmed"
        count += 1
    else:
        detail["trend"] = "Not Confirmed"

    if momentum_value is None:
        detail["momentum"] = "Unavailable"
    elif momentum_value >= MOMENTUM_CONFIRM_THRESHOLD_PCT:
        detail["momentum"] = "Confirmed"
        count += 1
    else:
        detail["momentum"] = "Not Confirmed"

    if vwap_confirmed_flag:
        detail["vwap"] = "Confirmed"
        count += 1
    else:
        detail["vwap"] = "Not Confirmed"

    required = DIRECTIONAL_CONFIRMATION_MIN_COUNT
    passed = (not DIRECTIONAL_CONFIRMATION_ENABLED) or (count >= required)
    return {"count": count, "required": required, "passed": passed, "detail": detail}


def quality_gate(factors: dict) -> bool:
    """
    HARD RISK GATES ONLY (as of 2026-07-17 rewrite).

    These three are non-negotiable safety checks, not "how good does
    this setup look" signals -- failing any one means the trade is
    structurally unsafe (illiquid, wrong risk profile, or theta-
    exposed) regardless of how strong the directional signals are.

    Previously this required ALL 9 factors (including trend/volume/
    OI/MTF/RR alignment) to be True simultaneously -- an unrealistic
    AND across 6 semi-independent signals that, in 26 consecutive
    live scan cycles, never once passed. The other factors are now
    scored on a weighted 0-100 scale via trade_score.py's Phase 3
    Trade Score Engine (continuous strengths, not the binary ones
    this factors dict still carries -- this dict is only used for the
    hard gates below plus reasons_from_factors()'s "why" text).
    config.ENTRY_SCORE is the live alert threshold on that score.

    UPDATE (2026-08-07): "premium_in_range_ok" added as a 4th hard
    gate -- config.PREMIUM_RANGE_ENABLED-gated, and only actually
    constrains symbols listed in config.PREMIUM_RANGE (see
    premium_in_range()'s own docstring), so this stays a no-op for
    every symbol not yet tuned. The candle-readiness check
    (candles_ready()) and the directional confirmation gate
    (directional_confirmation_status()) are DELIBERATELY NOT folded in
    here -- both are checked as separate, earlier gates in
    scanner.score_strike() (readiness first, since nothing else is
    meaningful to compute before it; directional confirmation after,
    since it needs several already-computed values) so each produces
    its own distinct REJECTED reason rather than one undifferentiated
    "hard_gate=FAIL".
    """
    required = ("liquidity_ok", "delta_ok", "expiry_theta_ok", "premium_in_range_ok")
    return all(factors.get(k) is True for k in required)


_REJECTION_LABELS = {
    "liquidity_ok": "Low Liquidity",
    "delta_ok": "Weak Delta (deep OTM)",
    "expiry_theta_ok": "Theta Risk Too High (near expiry, OTM)",
    "premium_in_range_ok": "Premium Outside Configured Range",
    "ema_aligned": "Weak Trend",
    "vwap_confirmed": "Against VWAP",
    "volume_confirmed": "Low Volume",
    "oi_building": "No OI Build-up",
    "mtf_confirmed": "15-min Not Confirmed",
    "risk_reward_ok": "Poor Risk Reward",
}


def rejection_reasons(
    factors: dict,
    hard_gate_keys=("liquidity_ok", "delta_ok", "expiry_theta_ok", "premium_in_range_ok"),
) -> list:
    """
    Phase 4: human-readable "why was this rejected" list for the
    REJECTED log line in scanner.py, built from the same `factors`
    dict quality_gate() and reasons_from_factors() already use --
    just the False entries instead of the True ones. Only the hard
    gate keys can actually cause a rejection (quality_gate() only
    requires those three), so this checks those first; the remaining
    keys are included too when False, purely as extra context on an
    otherwise-passing setup (they don't gate anything by themselves,
    see filters.quality_gate()'s docstring), matching the "Weak Trend
    / Wide Spread / Low OI" style example rejection output.
    """
    reasons = [_REJECTION_LABELS[k] for k in hard_gate_keys if factors.get(k) is not True]
    for k, label in _REJECTION_LABELS.items():
        if k in hard_gate_keys:
            continue
        if factors.get(k) is False:
            reasons.append(label)
    return reasons or ["Low Score"]


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
        "premium_in_range_ok": "Premium In Configured Range",
    }
    return [f"✓ {labels[k]}" for k in labels if factors.get(k) is True]
