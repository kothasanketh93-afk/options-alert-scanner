"""
btst_recommendation.py
------------------------
BTST/STBT Recommendation Engine -- ADVISORY ONLY.

This module NEVER places an order, NEVER modifies a broker position,
and NEVER carries a trade overnight by itself. It reads a single
already-open (or OPEN AT EOD) intraday trade dict -- the exact same
dict main.py/tracker.py/trade_lifecycle.py already maintain -- and
returns a Recommendation Object: YES/NO, BTST or STBT, a 0-100
confidence score, and the reasons behind it. A human still has to
place the actual overnight order manually; nothing downstream of this
module is allowed to do that automatically (see config.py's
BTST_RECOMMENDATION_ENABLED docstring).

DESIGN PRINCIPLE -- REUSE, DON'T RECOMPUTE: every one of the 11
factors the spec asks for (Trend, EMA, OI, HTF, Liquidity, Spread,
Volume, IV, Risk Reward, Market Strength, News Risk) is read off
fields that scanner.py / options_intelligence / decision_engine /
quant_intelligence / news_intelligence have ALREADY computed and
attached to the trade dict this cycle -- see the module docstrings of
scripts/filters.py, app/engines/options_intelligence/engine.py, and
app/engines/decision_engine/{quant_adapter,news_adapter}.py. This file
adds no new indicator math, no new option-chain read, no new broker
call -- it is a pure read + weighted-scoring layer, same posture as
trade_lifecycle.py's own docstring describes for itself.

BTST vs STBT: this codebase only ever BUYS options (calls or puts),
never shorts anything (see btst_main.py's own docstring) -- so every
carried position is mechanically a "buy today, sell/exit tomorrow".
The BTST/STBT label below is therefore a DIRECTIONAL-VIEW label, not a
different mechanic: a bullish CE carry is labeled BTST, a bearish PE
carry is labeled STBT, matching how these terms are colloquially used
for a bullish-vs-bearish overnight carry. This assumption is stated
here explicitly rather than hidden inside the scoring.

Recommendation Object shape (`evaluate_carry()`'s return value):
    {
        "recommendation": "YES" | "NO",
        "carry_type": "BTST" | "STBT" | None,   # None when recommendation == "NO"
        "confidence_score": int,                 # 0-100
        "reasons": [str, ...],                    # human-readable, PASS/FAIL/NA per factor
        "factor_breakdown": {factor_name: {"result": "PASS"|"FAIL"|"NA", "detail": str, "weight": int}},
        "hard_fails": [str, ...],                 # factors that force a NO regardless of score
        "generated_at": iso timestamp,
        "disclaimer": "Recommendation only. No auto carry -- a human must place the overnight order.",
    }
"""

from datetime import datetime, timezone, timedelta

import config

IST = timezone(timedelta(hours=5, minutes=30))

# Factor weights, out of 100. 11 factors per the spec (Trend, EMA, OI,
# HTF, Liquidity, Spread, Volume, IV, Risk Reward, Market Strength,
# News Risk). Market Strength / News Risk get half weight since they
# are context, not direct trade-setup signals -- everything else asks
# a direct pass/fail question about THIS trade's own setup.
_WEIGHTS = {
    "trend": 10,
    "ema": 10,
    "oi": 10,
    "htf": 10,
    "liquidity": 10,
    "spread": 10,
    "volume": 10,
    "iv": 10,
    "risk_reward": 10,
    "market_strength": 5,
    "news_risk": 5,
}
assert sum(_WEIGHTS.values()) == 100


def _why_reasons(trade: dict) -> list:
    return trade.get("why_reasons") or []


def _oi(trade: dict) -> dict:
    return trade.get("options_intelligence") or {}


def _decision(trade: dict) -> dict:
    return trade.get("decision") or {}


def _quant(trade: dict) -> dict:
    return _decision(trade).get("quant_intelligence") or {}


def _news(trade: dict) -> dict:
    return _decision(trade).get("news_intelligence") or {}


def _check(name: str, passed, detail: str) -> dict:
    """passed: True/False/None (None = data unavailable -> Not Applicable,
    scored as half credit rather than a fail, same convention
    quant_intelligence/probability/scorer.py already uses for optional
    Signal fields it treats as "no contribution" rather than an error)."""
    result = "PASS" if passed is True else ("FAIL" if passed is False else "NA")
    return {"result": result, "detail": detail, "weight": _WEIGHTS[name]}


def _check_trend(trade: dict) -> dict:
    reasons = _why_reasons(trade)
    direction = trade.get("direction")
    vwap_label = "Above VWAP" if direction == "CE" else "Below VWAP"
    if any(vwap_label in r for r in reasons):
        return _check("trend", True, f"Price confirmed {vwap_label.lower()} (session trend intact)")
    if reasons:
        return _check("trend", False, "VWAP trend confirmation was not present at entry")
    return _check("trend", None, "Trend confirmation data not available on this trade")


def _check_ema(trade: dict) -> dict:
    reasons = _why_reasons(trade)
    if any("EMA aligned" in r for r in reasons):
        return _check("ema", True, "EMA alignment confirmed at entry")
    if reasons:
        return _check("ema", False, "EMA was not aligned at entry")
    return _check("ema", None, "EMA data not available on this trade")


def _check_oi(trade: dict) -> dict:
    reasons = _why_reasons(trade)
    oi_intel = _oi(trade)
    if any("OI Build-up" in r for r in reasons):
        return _check("oi", True, "Strong OI build-up confirmed at entry")
    oi_score = oi_intel.get("oi_score")
    if oi_score is not None:
        return _check("oi", oi_score >= 55, f"Options Intelligence OI score: {oi_score}/100")
    return _check("oi", None, "OI data not available on this trade")


def _check_htf(trade: dict) -> dict:
    reasons = _why_reasons(trade)
    if any("15-minute Confirmed" in r for r in reasons):
        return _check("htf", True, "Higher timeframe (15-min) confirmation present at entry")
    if reasons:
        return _check("htf", False, "Higher timeframe (15-min) confirmation was missing at entry")
    return _check("htf", None, "HTF confirmation data not available on this trade")


def _check_liquidity(trade: dict) -> dict:
    oi_intel = _oi(trade)
    score = oi_intel.get("liquidity_score")
    if score is not None:
        return _check("liquidity", score >= 55, f"Options Intelligence liquidity score: {score}/100")
    reasons = _why_reasons(trade)
    if any("Excellent Liquidity" in r for r in reasons):
        return _check("liquidity", True, "Liquidity was rated excellent at entry")
    return _check("liquidity", None, "Liquidity data not available on this trade")


def _check_spread(trade: dict) -> dict:
    raw = _oi(trade).get("raw") or {}
    bid, ask = raw.get("bid"), raw.get("ask")
    if bid is not None and ask is not None and (bid + ask) > 0:
        mid = (bid + ask) / 2
        spread_pct = round((ask - bid) / mid * 100, 2) if mid else None
        if spread_pct is not None:
            ok = spread_pct <= config.BTST_RECOMMENDATION_MAX_SPREAD_PCT
            return _check("spread", ok, f"Bid-ask spread: {spread_pct}% of mid (limit {config.BTST_RECOMMENDATION_MAX_SPREAD_PCT}%)")
    return _check("spread", None, "Bid/ask quote not available on this trade")


def _check_volume(trade: dict) -> dict:
    reasons = _why_reasons(trade)
    if any("Volume Expansion" in r for r in reasons):
        return _check("volume", True, "Volume expansion confirmed at entry")
    raw = _oi(trade).get("raw") or {}
    volume = raw.get("volume")
    if volume is not None:
        return _check("volume", volume > 0, f"Last traded volume: {volume}")
    return _check("volume", None, "Volume data not available on this trade")


def _check_iv(trade: dict) -> dict:
    oi_intel = _oi(trade)
    iv_score = oi_intel.get("iv_score")
    abnormal = "Abnormal IV quote" in (oi_intel.get("warning_list") or [])
    if iv_score is not None:
        return _check("iv", (iv_score >= 45) and not abnormal,
                       f"Options Intelligence IV score: {iv_score}/100"
                       + (" (flagged abnormal)" if abnormal else ""))
    return _check("iv", None, "IV data not available on this trade")


def _check_risk_reward(trade: dict) -> dict:
    rr = _decision(trade).get("risk_reward") or {}
    ratio = rr.get("risk_reward_ratio")
    if ratio is not None:
        return _check("risk_reward", ratio >= config.BTST_RECOMMENDATION_MIN_RR,
                       f"Risk:Reward ratio {ratio} (minimum {config.BTST_RECOMMENDATION_MIN_RR})")
    reasons = _why_reasons(trade)
    if any(("No Nearby Resistance" in r) or ("No Nearby Support" in r) for r in reasons):
        return _check("risk_reward", True, "No nearby resistance/support against the position at entry")
    return _check("risk_reward", None, "Risk:Reward data not available on this trade")


def _check_market_strength(trade: dict) -> dict:
    quant = _quant(trade)
    regime = quant.get("regime")
    if not regime:
        return _check("market_strength", None, "Market regime data not available (Quant Intelligence disabled/unavailable)")
    direction = trade.get("direction")
    bullish_regimes = {"TRENDING_BULLISH", "BULLISH", "STRONG_BULLISH"}
    bearish_regimes = {"TRENDING_BEARISH", "BEARISH", "STRONG_BEARISH"}
    aligned = (direction == "CE" and regime in bullish_regimes) or (direction == "PE" and regime in bearish_regimes)
    return _check("market_strength", aligned, f"Market regime: {regime} (trade direction: {direction})")


def _check_news_risk(trade: dict) -> dict:
    news = _news(trade)
    if not news.get("enabled"):
        return _check("news_risk", None, "News Intelligence not available for this symbol")
    impact = news.get("impact")
    sentiment = news.get("sentiment")
    high_risk = str(impact).upper() == "HIGH"
    return _check("news_risk", not high_risk,
                   f"News impact: {impact}, sentiment: {sentiment}, articles: {news.get('article_count', 0)}")


_FACTOR_CHECKS = (
    ("trend", _check_trend),
    ("ema", _check_ema),
    ("oi", _check_oi),
    ("htf", _check_htf),
    ("liquidity", _check_liquidity),
    ("spread", _check_spread),
    ("volume", _check_volume),
    ("iv", _check_iv),
    ("risk_reward", _check_risk_reward),
    ("market_strength", _check_market_strength),
    ("news_risk", _check_news_risk),
)


def _hard_fails(trade: dict, breakdown: dict) -> list:
    """A small number of factors are treated as hard gates -- a FAIL on
    any of these forces an overall NO regardless of the weighted score,
    because they represent risk that specifically materializes
    OVERNIGHT (gap risk), not just intraday risk:
      - spread too wide (illiquid overnight, can't exit cleanly on a gap)
      - news risk HIGH (event risk that resolves overnight is exactly
        the risk BTST/STBT carry is exposed to that an intraday trade is not)
      - too close to expiry (theta/pin risk explodes overnight into
        the next session)
    """
    fails = []
    if breakdown["spread"]["result"] == "FAIL":
        fails.append("Spread too wide for a safe overnight exit")
    if breakdown["news_risk"]["result"] == "FAIL":
        fails.append("High-impact news risk resolves overnight")

    days_left = None
    oi_intel = _oi(trade)
    expiry_bucket = oi_intel.get("expiry_bucket")
    if expiry_bucket:
        expiry = trade.get("expiry")
        try:
            expiry_date = (
                datetime.fromisoformat(expiry).date() if "T" in str(expiry)
                else datetime.strptime(str(expiry), "%Y-%m-%d").date()
            )
            days_left = (expiry_date - datetime.now(IST).date()).days
        except (ValueError, TypeError):
            days_left = None
    if days_left is not None and days_left < config.BTST_RECOMMENDATION_MIN_DTE:
        fails.append(f"Only {days_left} day(s) to expiry -- overnight theta/pin risk too high")

    return fails


def evaluate_carry(trade: dict, now: datetime = None) -> dict:
    """
    Pure function: reads `trade` only, never mutates it, never calls a
    broker. Safe to call repeatedly (e.g. re-evaluated every remaining
    cycle of the day) -- caller decides whether/how often to re-run it
    and whether to notify again (see btst_recommendation's caller in
    main.py, which only notifies once per trade via messages_sent).
    """
    now = now or datetime.now(IST)

    breakdown = {}
    for name, fn in _FACTOR_CHECKS:
        breakdown[name] = fn(trade)

    hard_fails = _hard_fails(trade, breakdown)

    score = 0.0
    for name, info in breakdown.items():
        weight = info["weight"]
        if info["result"] == "PASS":
            score += weight
        elif info["result"] == "NA":
            score += weight * 0.5
        # FAIL contributes 0
    confidence_score = round(score)

    recommendation = "YES" if (confidence_score >= config.BTST_RECOMMENDATION_MIN_SCORE and not hard_fails) else "NO"
    direction = trade.get("direction")
    carry_type = None
    if recommendation == "YES":
        carry_type = "BTST" if direction == "CE" else "STBT"

    reasons = []
    for name, info in breakdown.items():
        label = name.replace("_", " ").upper()
        reasons.append(f"[{info['result']}] {label}: {info['detail']}")
    if hard_fails:
        reasons.extend(f"[HARD FAIL] {f}" for f in hard_fails)

    return {
        "recommendation": recommendation,
        "carry_type": carry_type,
        "confidence_score": confidence_score,
        "reasons": reasons,
        "factor_breakdown": breakdown,
        "hard_fails": hard_fails,
        "generated_at": now.isoformat(),
        # This string is deliberately shipped as data, not just a code
        # comment, so it also appears wherever this object is rendered
        # (Telegram, dashboard) -- see config.py's
        # BTST_RECOMMENDATION_ENABLED docstring for the enforcement side.
        "disclaimer": "Recommendation only. No auto carry -- a human must place the overnight order manually.",
    }


def recommend_for_trade(trade: dict, now: datetime = None) -> dict | None:
    """
    Caller-facing entry point (main.py). Returns None (does nothing)
    unless BOTH feature flags are on:
      - config.BTST_RECOMMENDATION_ENABLED (this engine at all)
      - the trade is actually OPEN AT EOD (trade_lifecycle's own flag) --
        this engine is for OPEN AT EOD trades ONLY, per the spec; it is
        never evaluated intraday or for an already-closed trade.
    Never mutates `trade` itself -- caller decides whether to attach the
    result (main.py stores it as trade["btst_recommendation"], the same
    additive-field pattern as trade["decision"]/trade["options_intelligence"]).
    """
    if not config.BTST_RECOMMENDATION_ENABLED:
        return None
    if not trade.get("_marked_open_at_eod"):
        return None
    return evaluate_carry(trade, now=now)
