"""
trade_score.py -- Phase 3: Professional Trade Score Engine.
------------------------------------------------------------------------------
Replaces grading.py's binary strengths (1.0/0.0 per factor -> weighted
0-100) with the phase-3 spec's continuous weighted model: Trend 20,
Volume 15, OI 15, Momentum 10, VWAP 10, EMA 10, Risk Reward 5,
Liquidity 5, Market Strength 5, HTF 5 (config.TRADE_SCORE_WEIGHTS).

grading.py is NOT deleted -- nothing outside scanner.py's active path
imports it, so it's left in place as a reference/fallback, but
scanner.score_strike() now calls this module instead.

WHY RSI/PCR AREN'T HERE: the phase-3 spec assumes both are already
calculated. Neither is, in this codebase (RSI is deliberately excluded
per the earlier V2.0 spec; PCR was never implemented) -- per an
explicit decision, this engine is built entirely from indicators
already computed elsewhere rather than bolting on two new ones.
"Market Strength" is filled by a broad-index (NIFTY) efficiency-ratio
read instead -- a reasonable stand-in for "is the overall tape moving
with conviction right now", built from the same efficiency_ratio()
math already used for this symbol's own Trend factor.

TELEGRAM DISPLAY: per an explicit decision, this REVERSES the earlier
V2.0 "no raw numeric score ever reaches Telegram" design -- Trade
Score, Probability, Confidence, Risk, Reward, and Expected R:R are now
all shown, matching the phase-3 spec's example output. See
telegram_notify.py's fmt_new_alert().
"""

from config import (
    TRADE_SCORE_WEIGHTS, TRADE_SCORE_GRADE_THRESHOLDS, CONFIDENCE_STARS_BY_TRADE_GRADE,
    TS_EMA_SPREAD_CEILING_PCT, TS_MOMENTUM_CEILING_PCT, TS_VWAP_DIST_CEILING_PCT,
    TS_OI_BUILD_CEILING_PCT, TS_VOLUME_CEILING_MULT, TS_LIQUIDITY_CEILING_MULT,
    TS_RR_BASELINE, MIN_OI, MIN_OPTION_VOLUME, MAX_SPREAD_PCT,
    STOCK_SYMBOLS, STOCK_MIN_OI, STOCK_MIN_OPTION_VOLUME,
    ATR_VOLATILE_PCT_THRESHOLD, EXPIRY_DAY_THRESHOLD_DAYS,
    PROBABILITY_BANDS,
    ENTRY_SCORE,
    CANDLESTICK_CONFIDENCE_PENALTY_ENABLED, CANDLESTICK_INDECISION_PATTERNS,
    CANDLESTICK_CONFIDENCE_PENALTY_PCT,
)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


# ---------------------------------------------------------------------------
# Continuous 0-1 strength factors. Each takes raw inputs scanner.py already
# gathers per strike (or per symbol) and normalizes against a config ceiling.
# ---------------------------------------------------------------------------

def trend_strength(efficiency_ratio_value):
    """
    efficiency_ratio() from indicators.py is already 0-1 -- this IS
    the trend-cleanliness read, no further scaling needed.

    BUGFIX (2026-08-07): used to do `_clamp01(efficiency_ratio_value or
    0.0)`, which coerced BOTH None (not enough candles yet) and a
    genuine 0.0 reading into the same 0.0 -- and worse, fed that 0.0
    straight into compute_trade_score() as an AVAILABLE factor instead
    of an excluded one, dragging the composite score down for a reason
    that had nothing to do with the setup (the exact class of bug the
    EMA factor's 2026-07-30 fix already addressed, just not extended
    here at the time). None now passes through -- compute_trade_score()
    already excludes None factors and renormalizes remaining weights,
    same as it does for `ema`.
    """
    if efficiency_ratio_value is None:
        return None
    return _clamp01(efficiency_ratio_value)


def ema_strength(ema9: float, ema21: float, price: float) -> float:
    if not price:
        return 0.0
    spread_pct = abs(ema9 - ema21) / price
    return _clamp01(spread_pct / TS_EMA_SPREAD_CEILING_PCT)


def volume_strength(option_volume: float, symbol: str = None) -> float:
    min_volume = STOCK_MIN_OPTION_VOLUME if symbol in STOCK_SYMBOLS else MIN_OPTION_VOLUME
    if not min_volume:
        return 0.0
    return _clamp01(option_volume / (min_volume * TS_VOLUME_CEILING_MULT))


def oi_build_strength(oi_change_pct: float) -> float:
    return _clamp01(abs(oi_change_pct or 0.0) / TS_OI_BUILD_CEILING_PCT)


def momentum_strength(signed_momentum_pct):
    """
    BUGFIX (2026-08-07): used to return 0.0 for "momentum unavailable"
    (not enough candles for momentum_pct()'s lookback -- see that
    function's own None-return docstring). None now passes through
    for compute_trade_score() to exclude/renormalize, instead of being
    scored as "confirmed zero momentum".
    """
    if signed_momentum_pct is None:
        return None
    return _clamp01(signed_momentum_pct / TS_MOMENTUM_CEILING_PCT)


def vwap_strength(signed_distance_pct) -> float:
    if signed_distance_pct is None:
        return 0.0
    return _clamp01(signed_distance_pct / TS_VWAP_DIST_CEILING_PCT)


def risk_reward_strength(target_distance: float, sl_distance: float) -> float:
    if not sl_distance:
        return 0.0
    rr_ratio = target_distance / sl_distance
    return _clamp01(rr_ratio / TS_RR_BASELINE)


def liquidity_strength(oi: float, volume: float, bid: float, ask: float,
                        premium: float, symbol: str = None) -> float:
    """
    Averages three sub-ratios (OI vs. min, volume vs. min, and spread
    tightness) -- same "strength, not just pass/fail" idea
    grading.py's original docstring described for this factor but
    never implemented (it was folded into a single binary
    liquidity_pass before). This is a SEPARATE, softer read from
    filters.liquidity_ok()'s hard gate -- a strike can pass the hard
    gate by a hair (liquidity_strength close to 0) or clear it
    comfortably (liquidity_strength close to 1).
    """
    is_stock = symbol in STOCK_SYMBOLS
    min_oi = STOCK_MIN_OI if is_stock else MIN_OI
    min_volume = STOCK_MIN_OPTION_VOLUME if is_stock else MIN_OPTION_VOLUME

    oi_ratio = _clamp01(oi / (min_oi * TS_LIQUIDITY_CEILING_MULT)) if min_oi else 0.0
    vol_ratio = _clamp01(volume / (min_volume * TS_LIQUIDITY_CEILING_MULT)) if min_volume else 0.0

    if premium:
        spread_pct = (ask - bid) / premium
        spread_ratio = _clamp01(1.0 - (spread_pct / MAX_SPREAD_PCT)) if MAX_SPREAD_PCT else 0.0
    else:
        spread_ratio = 0.0

    return (oi_ratio + vol_ratio + spread_ratio) / 3


def market_strength(broad_efficiency_ratio_value):
    """
    NIFTY's own efficiency_ratio() -- broad-tape conviction proxy,
    independent of THIS symbol's own trend (see trend_strength()).

    BUGFIX (2026-08-07): now that indicators.efficiency_ratio() can
    itself return None (not enough NIFTY candles yet, same as any
    other symbol early in the session), this None-check actually
    fires -- it used to be dead code, since the old efficiency_ratio()
    never returned anything but a float. Returns None (exclude from
    the weighted average) instead of the old 0.0.
    """
    if broad_efficiency_ratio_value is None:
        return None
    return _clamp01(broad_efficiency_ratio_value)


def htf_strength(mtf_confirmed):
    """
    BUGFIX (2026-08-07): mtf_confirmed is now tri-state (True/False/
    None) via option_chain.multi_timeframe_confirmation_detail() --
    None means "15-min EMA9/21 not yet computable", not "confirmed
    against the trade", and is excluded from the weighted average
    instead of scored as 0.0. The plain multi_timeframe_confirms()
    boolean (still used elsewhere unchanged) never returns None, so a
    caller passing that in unmodified still gets the old True->1.0/
    False->0.0 behavior exactly as before -- this signature is a
    strict superset, not a breaking change.
    """
    if mtf_confirmed is None:
        return None
    return 1.0 if mtf_confirmed else 0.0


# ---------------------------------------------------------------------------
# Composite score + derived display metrics
# ---------------------------------------------------------------------------

def compute_trade_score(strengths: dict) -> int:
    """
    strengths keys must match config.TRADE_SCORE_WEIGHTS: trend,
    volume, oi, momentum, vwap, ema, risk_reward, liquidity,
    market_strength, htf -- each a 0-1 float, OR None for a factor
    that's genuinely unavailable this cycle (e.g. EMA9/21 not yet
    computable -- see scanner.py's ema strengths line). A MISSING key
    still scores 0 for that factor rather than raising, matching
    example_quality_score()'s defensive .get(key, 0.0) convention in
    grading.py -- that convention is unchanged.

    BUGFIX (2026-07-30): an explicit None used to be coerced straight
    to 0.0 by this same .get(key, 0.0) default (0.0 only ever applied
    when the key is MISSING, not when it's present-but-None -- so
    scanner.py's old `else 0.0` for unavailable EMA meant `strengths.get(key,0.0)`
    call actually received a real 0.0 value up front and dragged the
    weighted score down as if EMA were confirmed-absent/bearish rather
    than simply not-yet-known. None now means "exclude this factor and
    its weight from the average", and the remaining weights are
    renormalized so the score is still out of 100 -- e.g. if EMA
    (weight 10) is None, the other 90 points' worth of weight is
    rescaled up to fill the full 100. Scoring math for every AVAILABE
    (non-None) factor is unchanged.
    """
    weighted_sum = 0.0
    available_weight = 0
    for key, weight in TRADE_SCORE_WEIGHTS.items():
        value = strengths.get(key, 0.0)
        if value is None:
            continue  # unavailable -- exclude from both numerator and denominator
        value = max(0.0, min(1.0, value))
        weighted_sum += value * weight
        available_weight += weight
    if available_weight == 0:
        return 0  # every factor unavailable -- nothing to score
    score = (weighted_sum / available_weight) * 100
    return round(score)


def compute_grade(trade_score: int) -> str:
    for threshold, grade in TRADE_SCORE_GRADE_THRESHOLDS:
        if trade_score >= threshold:
            return grade
    return "Reject"


def compute_probability_pct(trade_score: int) -> int:
    """
    Simple linear rescaling of trade_score -- NOT a probability, NOT
    backtested against any outcome data. Kept ONLY as a display/ranking
    heuristic (e.g. sorting candidates within the same grade tier) --
    see calibrated_probability_pct() below for the ONLY function
    permitted to feed the alert-eligibility probability gate.

    BUGFIX (2026-08-12) -- P0 audit, "probability must not be
    fabricated": this function's OWN docstring already said "NOT a
    backtested probability model" since it was written, and yet
    scanner.score_strike() was feeding its output directly into
    candidate["probability_pct"] -- the exact field main.py's
    MIN_ALERT_PROBABILITY gate compared against 70, and the exact
    field the Telegram alert displayed as "Probability: 71% (Potential
    Trade)". A monotonic rescaling of trade_score is not a probability
    just because it's labeled one and bounded to a percentage -- it
    encodes ONLY "how well did this candidate's own factor scores add
    up", never "in similar past setups, how often did target beat
    stop". Renamed in spirit (not in name, to avoid an unrelated mass
    rename of every existing caller/test that already treats this as
    "the trade_score rescaling function") -- its role in the pipeline
    is now display/ranking ONLY. scanner.score_strike() no longer uses
    this function's output for candidate["probability_pct"] -- see
    calibrated_probability_pct() and that call site's own comment.
    """
    return round(_clamp01((trade_score - 60) / 40) * 32 + 65)


def calibrated_probability_pct(context: dict):
    """
    THE ONLY function permitted to feed the alert-eligibility
    probability gate (main.py's config.MIN_ALERT_PROBABILITY check,
    and candidate["probability_pct"] in the Telegram alert payload).
    Returns an int 0-100, or None when a calibrated estimate cannot be
    produced -- callers MUST treat None as UNAVAILABLE, not as "assume
    0" or "fall back to trade_score" (see scanner.score_strike()'s own
    call site: None here means NO ALERT, unconditionally, however high
    trade_score/every other gate scored).

    WHAT THIS IS MEANT TO ANSWER: P(target reached before stop-loss),
    conditional on the setup's own characteristics -- NOT "how good did
    this candidate's factor scores add up" (that question belongs to
    trade_score/compute_probability_pct() above, a completely different
    concept this repository previously conflated with this one). A
    genuine implementation would look up a calibrated win-rate from
    HISTORICAL OUTCOME DATA (tracker.py/state.py's SL_HIT/T1_HIT/
    T1_THEN_SL trade records already exist per-trade), bucketed by
    characteristics meaningfully similar to `context` below -- e.g.
    moneyness x direction x RR-bucket x extension-state x volatility-
    regime x time-of-day -- with enough resolved trades PER BUCKET for
    the resulting rate to mean anything statistically (a bucket with 3
    resolved trades is not a probability estimate, it's noise dressed
    up as one).

    CURRENT STATE (2026-08-12): this repository has per-trade outcome
    records (state.py's trade dicts reach a `final_status` of SL_HIT/
    T1_THEN_SL/T1_SUCCESS/etc via tracker.py) but NO aggregation into a
    bucketed calibration table anywhere, and no evidence of enough
    resolved trades per plausible bucket to fit one reliably yet (the
    daily summaries reviewed this session showed single-digit resolved-
    trade counts per day). Building a real calibration model on that
    little data would itself be fabrication with extra steps -- so this
    function unconditionally returns None for now. This is the
    intentional, safe default per this system's own stated principle:
    prefer PROBABILITY UNAVAILABLE over a fabricated number, even a
    plausible-looking one. When a real historical dataset and
    calibration methodology exist, this is the ONE place to wire them
    in -- every caller already treats None as "no alert" correctly, so
    filling this in later requires no changes anywhere else in the
    pipeline.

    context: a dict describing the setup this probability would be
        FOR -- deliberately accepted now (rather than added later when
        real calibration lands) so callers already pass everything a
        real model would need, and so this function's SIGNATURE change
        doesn't become a second migration later. Expected keys (all
        optional -- a real implementation would decide which actually
        matter): "symbol", "direction", "moneyness", "expiry_days",
        "trend_value", "momentum_value", "market_regime", "rr_ratio",
        "time_of_day_bucket". Unused by this stub implementation.
    """
    return None


def probability_label(probability_pct: int) -> str:
    """
    Bands compute_probability_pct()'s output per config.PROBABILITY_BANDS
    (Ignore / Watchlist / Potential Trade / High Probability Trade /
    Strong Institutional Setup) -- display/transparency only, walked
    the same way compute_grade() walks TRADE_SCORE_GRADE_THRESHOLDS.
    Not a second gate: config.TELEGRAM_MIN_PROBABILITY (checked by the
    caller, not here) is what actually decides whether a Telegram
    message goes out.
    """
    for threshold, label in PROBABILITY_BANDS:
        if probability_pct >= threshold:
            return label
    return "Ignore"


# Human-readable labels for the scoring-reasons breakdown below --
# deliberately separate from _CHECKLIST_LABELS further down, since that
# one is a flat ✅/⚠️ pass-mark list while this one needs a directional,
# case-specific phrase per factor (matching the phase-4 spec's "+15
# Trend Alignment" / "-5 Slightly High RSI" style example output).
_REASON_LABELS = {
    "trend": "Trend Alignment",
    "volume": "Volume Spike",
    "oi": "OI Build-up",
    "momentum": "Momentum",
    "vwap": "VWAP Positioning",
    "ema": "EMA Alignment",
    "risk_reward": "Risk:Reward",
    "liquidity": "Liquidity",
    "market_strength": "Market Strength",
    "htf": "Higher-Timeframe Confirmation",
}


def scoring_reasons(strengths: dict) -> list:
    """
    Turns the same 0-1 `strengths` dict compute_trade_score() consumes
    into a signed, per-factor points breakdown that sums to
    compute_trade_score()'s output -- e.g. "+15 Trend Alignment", "-5
    Liquidity". A factor contributes its full weight at strength 1.0;
    below half-strength it's shown as a NEGATIVE number (how many
    points short of that factor's own midpoint it is), so weak
    contributors read as visible deductions rather than just small
    positives -- matching the phase-4 spec's example mix of + and -
    lines. Sorted strongest-contribution-first. This is pure display
    math over numbers scanner.py already computed; it does not change
    compute_trade_score()'s own total.
    """
    reasons = []
    for key, weight in TRADE_SCORE_WEIGHTS.items():
        raw_value = strengths.get(key, 0.0)
        if raw_value is None:
            continue  # unavailable this cycle (e.g. EMA) -- no reason line, same exclusion as compute_trade_score()
        value = max(0.0, min(1.0, raw_value))
        label = _REASON_LABELS.get(key, key)
        midpoint = weight / 2
        points = value * weight
        if points >= midpoint:
            reasons.append((points, f"+{round(points)} {label}"))
        else:
            reasons.append((points, f"-{round(midpoint - points)} {label}"))
    reasons.sort(key=lambda r: r[0], reverse=True)
    return [text for _, text in reasons]


def probability_trend(history_pct: list) -> str:
    """
    "Increasing" / "Stable" / "Decreasing" off the last two readings in
    a same-day probability history list (oldest-first, as maintained by
    state.py's probability_history). Fewer than 2 readings -> "Stable"
    (nothing to compare yet). A flat 1-point wobble either way still
    reads as "Stable" -- only a >=2-point move counts as a real
    direction change, so display doesn't flap on scan-to-scan noise.
    """
    if len(history_pct) < 2:
        return "Stable"
    delta = history_pct[-1] - history_pct[-2]
    if delta >= 2:
        return "Increasing"
    if delta <= -2:
        return "Decreasing"
    return "Stable"


def compute_confidence_score(trade_score: int) -> float:
    """Trade score /10, one decimal -- e.g. 92 -> 9.2."""
    return round(trade_score / 10, 1)


def compute_risk_score(atr_pct: float, moneyness: str, expiry_days: int) -> float:
    """
    0-10, HIGHER = riskier (matches the spec's "Risk: 2.3/10" example,
    where a low number is good). Same inputs grading.py's old
    risk_rating() used (ATR% of price, moneyness), plus days-to-expiry
    since theta risk compounds specifically near expiry (see
    filters.expiry_theta_ok()'s docstring).
    """
    score = 2.0  # baseline -- every options trade carries some risk
    if atr_pct >= ATR_VOLATILE_PCT_THRESHOLD:
        score += 3.0
    elif atr_pct >= ATR_VOLATILE_PCT_THRESHOLD * 0.5:
        score += 1.5

    if moneyness == "OTM":
        score += 2.0
    elif moneyness == "ITM":
        score -= 1.0

    if expiry_days is not None and expiry_days <= EXPIRY_DAY_THRESHOLD_DAYS:
        score += 1.5

    return round(max(0.5, min(10.0, score)), 1)


def compute_reward_score(rr_ratio: float) -> float:
    """0-10, HIGHER = better reward -- rr_ratio 3.0 (target 3x the SL
    distance) or more scores a full 10; scales linearly below that."""
    if rr_ratio is None or rr_ratio <= 0:
        return 0.0
    return round(min(10.0, (rr_ratio / 3.0) * 10), 1)


def confidence_display(grade: str) -> tuple:
    """Backward-compat stars/label pair, re-keyed to the new grade
    vocabulary -- trade dicts still carry confidence_stars/
    confidence_label alongside the new numeric confidence_score."""
    return CONFIDENCE_STARS_BY_TRADE_GRADE.get(grade, ("☆☆☆☆☆", "N/A"))


def risk_emoji_label(risk_score: float) -> tuple:
    """Emoji/label pair for risk_score (0-10, higher = riskier) --
    same three-bucket idea as grading.py's old risk_rating(), just
    driven off the new continuous risk_score instead of a fresh
    from-scratch calc."""
    if risk_score <= 3.0:
        return "🟢", "Low"
    if risk_score <= 6.0:
        return "🟡", "Medium"
    return "🔴", "High"


def expected_rr_text(entry_premium: float, stop_loss: float, target: float) -> str:
    """Formats as "1:X.X" per the spec's Telegram example. Returns
    "1:0" if the SL distance is zero/negative (shouldn't happen --
    dynamic_trade_levels() already floors SL below premium -- but this
    is display formatting, not a gate, so it degrades rather than
    raises)."""
    sl_distance = entry_premium - stop_loss
    target_distance = target - entry_premium
    if sl_distance <= 0:
        return "1:0"
    return f"1:{round(target_distance / sl_distance, 1)}"


_CHECKLIST_LABELS = [
    ("trend", "Trend"),
    ("volume", "Volume"),
    ("oi", "OI"),
    ("momentum", "Momentum"),
    ("vwap", "VWAP"),
    ("ema", "EMA"),
    ("risk_reward", "Risk Reward"),
    ("liquidity", "Liquidity"),
    ("market_strength", "Market Strength"),
    ("htf", "HTF"),
]


def factor_checklist(strengths: dict, pass_threshold: float = 0.5) -> list:
    """
    Telegram checklist lines (✅/⚠️ per factor) matching the spec's
    "Trend ✅ / Volume ✅ / ..." block. pass_threshold is a display
    cutoff only -- it does not gate whether the trade is alerted at
    all (config.ENTRY_SCORE on the composite trade_score already does
    that); a factor below threshold just shows ⚠️ instead of ✅ so the
    reader can see which specific inputs were weaker contributors.
    """
    lines = []
    for key, label in _CHECKLIST_LABELS:
        value = strengths.get(key, 0.0)
        if value is None:
            # BUGFIX (2026-08-11): used to force None -> 0.0 -> shown as
            # a permanent "⚠️" -- correct for a factor that's merely
            # unavailable THIS cycle (e.g. EMA before enough candles),
            # but wrong for "htf", which is now ALWAYS None (no genuine
            # independent higher-timeframe source exists in this
            # codebase -- see filters.directional_confirmation_status()'s
            # docstring). Showing "HTF ⚠️" on every single alert forever
            # reads as "checked and weak", not "never checked" -- exactly
            # the misleading-terminology problem flagged in review.
            # Skipped entirely instead, matching scoring_reasons()'s
            # existing "no reason line for an unavailable factor" behavior
            # just above -- both display functions now agree.
            continue
        mark = "✅" if value >= pass_threshold else "⚠️"
        lines.append(f"{label} {mark}")
    return lines


# ---------------------------------------------------------------------------
# Win-Rate Improvement Pass (2026-08-07): adaptive entry score, candlestick
# confidence penalty, structured rejection logging.
# ---------------------------------------------------------------------------

# REMOVED (2026-08-10, "do we need all this" review): effective_entry_score()
# used to live here -- a session-time/trend-aware adaptive floor,
# independent of detect_setups()'s own regime-based floor
# (config.ENTRY_SCORE + config.REGIME_SCORE_PENALTY[regime]). Removed
# as a redundant, disagreeing SECOND floor -- see scanner.score_strike()'s
# comment at its old call site for the specific contradiction this
# caused. detect_setups()'s regime-aware floor is now the SOLE
# adaptive threshold; this function's static ENTRY_SCORE baseline
# (via `grade == "Reject"`) is the only check inside score_strike()
# itself.


def apply_candlestick_penalty(score: int, candlestick_read: dict) -> tuple:
    """
    Applies a bounded, multiplicative score penalty (never a hard
    reject -- per explicit instruction) when the most recently formed
    candle is one of config.CANDLESTICK_INDECISION_PATTERNS (Doji,
    Spinning Top, Shooting Star by default -- see that config entry's
    own comment on why "Hanging Man" is deliberately not included:
    candlestick_patterns.py doesn't implement it, it's geometrically
    identical to Hammer and needs preceding-trend context that
    module's single-formation detectors don't track).

    Checks candlestick_read["patterns_detected"] (the full list for
    the most recent candle, e.g. a Marubozu close can also complete an
    Engulfing pattern -- see candlestick_patterns.detect()) rather than
    just dominant_pattern, so a co-occurring indecision pattern isn't
    missed just because a stronger directional pattern also matched
    the same candle and won the "dominant" ranking.

    Returns (adjusted_score, penalty_applied: bool). adjusted_score is
    unchanged (same object identity in spirit, just an int) when
    disabled, no read available, or no indecision pattern matched.
    """
    if not CANDLESTICK_CONFIDENCE_PENALTY_ENABLED:
        return score, False
    if not candlestick_read or not candlestick_read.get("enabled"):
        return score, False
    detected = set(candlestick_read.get("patterns_detected") or [])
    if not detected & CANDLESTICK_INDECISION_PATTERNS:
        return score, False
    adjusted = round(score * (1 - CANDLESTICK_CONFIDENCE_PENALTY_PCT))
    return adjusted, True


def rejection_detail_lines(
    candle_readiness_ok: bool,
    directional_status: dict,
    score: int,
    min_score_required: int,
    grade_reject: bool,
) -> list:
    """
    Structured, itemized rejection reasons for the REJECTED log line,
    replacing the old generic "Low Score (52/100, floor=55)" text with
    per-factor detail -- matches the requested log shape:
        Direction confirmations: 1/4
        EMA (15m): Not Ready
        EMA (5m): Not Ready
        Trend: Insufficient candles
        Momentum: Unavailable
        Score below threshold

    NOTE (2026-08-11): a "HTF: ..." line used to appear here too --
    removed along with the duplicate "htf" signal in filters.
    directional_confirmation_status() (see that function's docstring).
    "Direction confirmations" is now out of 4, not 5.

    Only called AFTER the candle-readiness and directional-confirmation
    gates have already been evaluated (scanner.score_strike() calls
    this once per strike at the point of rejection, whichever gate
    actually failed) -- candle_readiness_ok is included so a
    readiness-gate rejection and a directional-confirmation-gate
    rejection both render through this one formatter instead of two
    differently-shaped log lines.
    """
    lines = []
    if not candle_readiness_ok:
        lines.append("Insufficient candle history")
        return lines

    detail = directional_status["detail"]
    lines.append(f"Direction confirmations: {directional_status['count']}/4 "
                 f"(need {directional_status['required']})")
    lines.append(f"EMA (15m): {detail['ema']}")
    lines.append(f"EMA (5m): {detail.get('m5', 'Not Ready')}")
    lines.append(f"Trend: {detail['trend']}")
    lines.append(f"Momentum: {detail['momentum']}")
    lines.append(f"VWAP: {detail['vwap']}")
    lines.append(
        f"Structural confirmation (EMA-5m/EMA-15m): "
        f"{directional_status.get('structural_confirmed_count', 0)}/"
        f"{directional_status.get('structural_required', 1)} "
        f"({'OK' if directional_status.get('structural_ok', True) else 'FAIL'})"
    )

    if not directional_status["passed"]:
        return lines  # directional gate itself is why this was rejected

    if grade_reject or score < min_score_required:
        lines.append(f"Score below threshold ({score}/100, required={min_score_required})")

    return lines
