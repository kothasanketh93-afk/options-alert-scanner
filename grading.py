"""
grading.py -- Quality score, grade, confidence display, risk rating.
------------------------------------------------------------------------
Split out of scanner.py. No numeric score reaches Telegram (per V2.0
spec) -- these turn the internal 0-100 quality score into "grade" +
"confidence_stars" + "risk_rating" for the trade dict.
"""

from config import ATR_VOLATILE_PCT_THRESHOLD, GRADE_THRESHOLDS, CONFIDENCE_DISPLAY


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
