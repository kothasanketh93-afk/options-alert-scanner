"""
app/engines/options_intelligence/engine.py -- Part 5 entry point.
------------------------------------------------------------------------------
analyze() is the ONE function scanner.py needs to call. It takes what
score_strike() has ALREADY fetched/computed this cycle (the full chain,
the extracted leg, spot price, expiry, oi_history) -- no new broker
call is made here, per the "avoid repeated option chain
downloads/repeated Greek calculations/repeated OI processing"
performance requirement: every input below is something score_strike()
was already holding in a local variable.

HOW THIS PLUGS INTO scanner.score_strike() (additive, opt-in):

    from app.engines.options_intelligence import engine as options_intel

    ...
    oi_intel = options_intel.analyze(
        symbol=symbol, direction=direction, moneyness=moneyness,
        offset_index=offset_index, strike=strike, leg=leg, chain=chain,
        spot_price=spot_price, expiry=expiry, oi_history=oi_history,
        oi_delta_pct=oi_delta_pct,
    )
    setup["options_intelligence"] = oi_intel   # additive field on the trade dict
    if config.OPTIONS_INTELLIGENCE_FILTER_ENABLED and not oi_intel["accepted"]:
        return None   # same style as the existing hard-gate rejection above

Nothing above changes what already runs when
config.OPTIONS_INTELLIGENCE_ENABLED or
config.OPTIONS_INTELLIGENCE_FILTER_ENABLED are left at their defaults
(True / False) -- the object is computed and attached for visibility,
but never rejects a trade unless the filter flag is explicitly flipped
on. See config.py's docstring on this section for the full rollout
rationale.
"""

from datetime import datetime, timezone, timedelta

from logging_setup import get_logger
from config import OPTIONS_INTELLIGENCE_ENABLED

from . import chain_metrics, greeks_iv, liquidity_premium, strike_expiry, momentum, scoring

_logger = get_logger("options_intelligence")
IST = timezone(timedelta(hours=5, minutes=30))

# Per-PROCESS PCR history, same scope/rationale as option_chain.py's
# module-level caches -- keyed by (symbol, expiry) since PCR is a
# whole-chain read, not a per-strike one.
_pcr_history = {}


def reset_state():
    """Test helper -- clears every per-process cache this package
    owns (this module's PCR history, plus greeks_iv/momentum's own).
    Not used by the live scanner (a fresh process already starts
    clean); exists so unit tests can run several independent cases in
    one process."""
    _pcr_history.clear()
    greeks_iv.reset_iv_history()
    momentum.reset_history()


def _days_to_expiry_safe(expiry: str):
    """
    Same 'YYYY-MM-DD' -> days-remaining arithmetic as
    option_chain.days_to_expiry() -- deliberately NOT imported from
    there: option_chain.py pulls in market_data.py at import time,
    which in turn constructs live Angel One/Upstox broker clients
    (angelone.py/upstox.py, which need pyotp + real credentials to
    even import). That's the right coupling for option_chain.py itself
    (it genuinely calls the broker elsewhere in that module), but this
    package should stay independently importable/testable with zero
    broker/network dependency -- same "can run anywhere, including CI"
    bar app/engines/early_breakout/tests/test_engine.py already holds
    itself to. This is trivial stdlib date arithmetic, not business
    logic -- reimplementing option_chain.days_to_expiry()'s one-line
    formula here avoids that whole transitive import for a date
    subtraction.
    """
    try:
        expiry_date = datetime.strptime(expiry, "%Y-%m-%d").date()
        return (expiry_date - datetime.now(IST).date()).days
    except Exception:
        return None


def analyze(symbol: str, direction: str, moneyness: str, offset_index: int,
            strike, leg: dict, chain: list, spot_price: float, expiry: str,
            oi_history: dict, oi_delta_pct: float = None) -> dict:
    """
    Returns the Standard Output Object (the "Options Intelligence
    Object") -- every downstream consumer (Telegram formatting, EOD
    reporting, the optional filter above) should read from this dict
    rather than recomputing any of it.

    oi_delta_pct: pass scanner.py's already-computed
    filters.oi_change_pct(oi_history, leg["instrument_key"], leg["oi"])
    result if you have it (score_strike() already calls this before
    oi_is_building() mutates oi_history) -- avoids a second, redundant
    OI-history read/mutate for the same instrument_key in the same
    cycle. If not supplied, it's computed here instead (read-only,
    does not mutate oi_history).
    """
    now = datetime.now(IST)
    warnings = []

    if not OPTIONS_INTELLIGENCE_ENABLED:
        return {
            "enabled": False,
            "timestamp": now.isoformat(),
        }

    instrument_key = leg.get("instrument_key")
    premium = leg.get("ltp")
    oi = leg.get("oi") or 0.0
    volume = leg.get("volume") or 0.0
    bid, ask = leg.get("bid"), leg.get("ask")
    delta, gamma, theta, vega, iv = (
        leg.get("delta"), leg.get("gamma"), leg.get("theta"), leg.get("vega"), leg.get("iv"),
    )

    if oi_delta_pct is None:
        # In the live scanner.score_strike() path this is ALWAYS supplied
        # (it already calls filters.oi_change_pct() before oi_is_building()
        # mutates oi_history -- see that function's docstring) -- this is
        # a read-only, defensive fallback for any OTHER caller of analyze()
        # that hasn't computed it yet. Reimplements filters.oi_change_pct()'s
        # one-line formula directly rather than importing it, for the same
        # reason _days_to_expiry_safe() above reimplements
        # option_chain.days_to_expiry() instead of importing it: filters.py
        # pulls in option_chain.py -> market_data.py -> live broker clients
        # at import time, which this package deliberately stays independent
        # of (see _days_to_expiry_safe()'s docstring).
        previous = (oi_history or {}).get(instrument_key)
        oi_delta_pct = 0.0 if not previous or previous <= 0 else (oi - previous) / previous

    # --- Option Chain / PCR / Max Pain / OI Engines (whole-chain reads) ---
    t_call_oi = chain_metrics.total_call_oi(chain)
    t_put_oi = chain_metrics.total_put_oi(chain)
    pcr_value = chain_metrics.overall_pcr(chain)
    strike_pcr_value = chain_metrics.strike_pcr(chain, strike)
    pcr_key = (symbol, expiry)
    pcr_change = chain_metrics.intraday_pcr_change(pcr_value, _pcr_history.get(pcr_key))
    _pcr_history[pcr_key] = pcr_value
    bias = chain_metrics.market_bias(pcr_value)
    pcr_score_value = chain_metrics.pcr_score(pcr_value, direction)

    max_pain = chain_metrics.max_pain_strike(chain)
    max_pain_score_value = chain_metrics.max_pain_score(spot_price, max_pain, direction)

    oi_concentration_rows = chain_metrics.oi_concentration(chain, direction)
    oi_score_value = chain_metrics.oi_strength_score(oi, oi_delta_pct, oi_concentration_rows, strike)
    buildup_label = chain_metrics.leg_buildup_label(oi_delta_pct or 0.0, (oi_delta_pct or 0.0))

    # --- Greeks / IV Engines ---
    greeks_score_value = greeks_iv.greeks_score(gamma, theta, premium, vega)
    risk_emoji, risk_label = greeks_iv.risk_rating(gamma, theta, premium)
    greeks_iv.record_iv_reading(instrument_key, iv)
    iv_score_value = greeks_iv.iv_score(instrument_key, iv)
    iv_abnormal = greeks_iv.is_iv_abnormal(iv)
    if iv_abnormal:
        warnings.append("Abnormal IV quote")

    # --- Liquidity / Premium Engines ---
    liquidity_score_value = liquidity_premium.liquidity_score(oi, volume, bid, ask, premium, symbol)
    premium_score_value = liquidity_premium.premium_score(spot_price, strike, direction, premium)
    premium_info = liquidity_premium.premium_breakdown(spot_price, strike, direction, premium)

    # --- Strike Selection / Expiry Analysis Engines ---
    strike_label = strike_expiry.strike_depth_label(moneyness, offset_index)
    strike_score_value = strike_expiry.strike_score(moneyness, offset_index, delta)
    days_left = _days_to_expiry_safe(expiry)
    expiry_bucket_label = strike_expiry.expiry_bucket(days_left)
    expiry_volatility = strike_expiry.expiry_volatility_label(days_left)
    expiry_score_value = strike_expiry.expiry_score(days_left, moneyness)

    # --- Options Momentum Engine ---
    momentum.record_reading(instrument_key, premium=premium, oi=oi, volume=volume, iv=iv)
    momentum_score_value = momentum.options_momentum_score(instrument_key, direction)

    sub_scores = {
        "oi": oi_score_value,
        "pcr": pcr_score_value,
        "max_pain": max_pain_score_value,
        "greeks": greeks_score_value,
        "iv": iv_score_value,
        "liquidity": liquidity_score_value,
        "premium": premium_score_value,
        "strike": strike_score_value,
        "expiry": expiry_score_value,
        "momentum": momentum_score_value,
    }

    # --- Options Confirmation / Scoring / Filter Engines ---
    confidence = scoring.options_confidence(sub_scores)
    composite_score = scoring.options_intelligence_score(sub_scores)
    accepted, rejection_reasons = scoring.options_filter(composite_score, sub_scores, iv_abnormal)
    if not accepted:
        warnings.extend(r for r in rejection_reasons if r != "Passed")

    result = {
        "enabled": True,
        "timestamp": now.isoformat(),
        "symbol": symbol,
        "direction": direction,
        "strike": strike,
        "expiry": expiry,
        "oi_score": round(oi_score_value * 100),
        "pcr_score": round(pcr_score_value * 100),
        "max_pain_score": round(max_pain_score_value * 100),
        "greeks_score": round(greeks_score_value * 100),
        "iv_score": round(iv_score_value * 100),
        "liquidity_score": round(liquidity_score_value * 100),
        "premium_score": round(premium_score_value * 100),
        "strike_recommendation": strike_label,
        "expiry_score": round(expiry_score_value * 100),
        "options_intelligence_score": composite_score,
        "confidence": confidence,
        "warning_list": warnings,
        "accepted": accepted,
        "rejection_reasons": [] if accepted else rejection_reasons,
        # Extra display/debug detail -- not part of the 13 required
        # fields above, but cheap to carry since it's all already computed:
        "total_call_oi": t_call_oi,
        "total_put_oi": t_put_oi,
        "overall_pcr": round(pcr_value, 3),
        "strike_pcr": round(strike_pcr_value, 3),
        "pcr_change": round(pcr_change, 3),
        "market_bias": bias,
        "max_pain": max_pain,
        "oi_buildup": buildup_label,
        "risk_rating": (risk_emoji, risk_label),
        "expiry_bucket": expiry_bucket_label,
        "expiry_volatility": expiry_volatility,
        "premium_breakdown": premium_info,
        # Part 11 (Quant Intelligence) integration, added additively:
        # these are local variables this function ALREADY computed
        # above for scoring (oi/volume/iv/bid/ask/oi_delta_pct) but
        # previously discarded once the *_score fields were derived.
        # Exposing them here lets decision_engine/quant_adapter.py
        # build a Signal from real per-leg reads without a second
        # broker/chain fetch or re-deriving them from the *_score
        # values -- "reuse existing market analysis, do not duplicate
        # calculations" per the Part 11 merge spec. No existing key
        # above is changed; this is a pure addition.
        "raw": {
            "oi": oi,
            "volume": volume,
            "iv": iv,
            "bid": bid,
            "ask": ask,
            "oi_change_pct": round(oi_delta_pct * 100, 3) if oi_delta_pct is not None else None,
        },
    }

    _logger.info(
        f"OPTIONS_INTEL {symbol} {direction} {moneyness} strike={strike} | "
        f"OI={result['oi_score']} PCR={result['pcr_score']} MaxPain={result['max_pain_score']} "
        f"Greeks={result['greeks_score']} IV={result['iv_score']} Liquidity={result['liquidity_score']} "
        f"Premium={result['premium_score']} Strike={strike_label} Expiry={result['expiry_score']} | "
        f"Score={composite_score}/100 Confidence={confidence} Decision={'ACCEPT' if accepted else 'REJECT'} "
        f"Warnings={warnings or 'none'}"
    )
    return result
