"""
chain_metrics.py -- Option Chain Engine, PCR Engine, Max Pain Engine,
Open Interest Engine.
------------------------------------------------------------------------------
Everything here reads the FULL chain list market_data.get_option_chain()
already returned (and cached, see that function's docstring --
_option_chain_cache, 180s TTL) for scanner.score_strike()'s own leg
lookup. No new broker call is made anywhere in this module -- these are
pure-Python aggregates over data already in memory, satisfying the
"Shared Option Chain Cache / avoid repeated option chain downloads"
performance requirement without adding a second cache: the existing one
already covers this.

Each public function is defensive about missing/None fields (both
brokers can legitimately omit oi/volume/greeks for a given leg -- see
option_chain.py's docstring on Angel One Greeks gaps) -- a strike with
incomplete data is simply excluded from the aggregate rather than
raising or corrupting the total.
"""

from config import (
    PCR_BULLISH_MAX, PCR_BEARISH_MIN, MAX_PAIN_DISTANCE_CEILING_PCT,
    OI_CONCENTRATION_TOP_N_STRIKES, OI_BUILD_THRESHOLD_PCT,
)


def _num(value):
    """None/missing -> 0.0, otherwise float(value). Never raises."""
    try:
        return float(value) if value is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------------------
# Option Chain Engine
# ---------------------------------------------------------------------------

def total_call_oi(chain: list) -> float:
    return sum(_num(row.get("call_oi")) for row in chain)


def total_put_oi(chain: list) -> float:
    return sum(_num(row.get("put_oi")) for row in chain)


def total_call_oi_change(chain: list) -> float:
    """Sum of call_oi - call_prev_oi across every strike that has both
    (Upstox-only field, per option_chain.py's docstring -- 0.0 rows
    without call_prev_oi contribute nothing rather than skewing the
    total negative)."""
    total = 0.0
    for row in chain:
        prev = row.get("call_prev_oi")
        if prev is None:
            continue
        total += _num(row.get("call_oi")) - _num(prev)
    return total


def total_put_oi_change(chain: list) -> float:
    total = 0.0
    for row in chain:
        prev = row.get("put_prev_oi")
        if prev is None:
            continue
        total += _num(row.get("put_oi")) - _num(prev)
    return total


def leg_buildup_label(oi_change_pct_value: float, price_change_pct: float) -> str:
    """
    Classic four-quadrant OI/price read:
      Long Build-up    -- price up,   OI up
      Short Build-up    -- price down, OI up
      Short Covering    -- price up,   OI down
      Long Unwinding    -- price down, OI down
    Either input near zero (inside OI_BUILD_THRESHOLD_PCT for OI, or a
    symmetric small-move band for price) reads as "Neutral" rather than
    guessing a direction from noise.
    """
    oi_up = oi_change_pct_value >= OI_BUILD_THRESHOLD_PCT
    oi_down = oi_change_pct_value <= -OI_BUILD_THRESHOLD_PCT
    price_up = price_change_pct >= OI_BUILD_THRESHOLD_PCT
    price_down = price_change_pct <= -OI_BUILD_THRESHOLD_PCT

    if price_up and oi_up:
        return "Long Build-up"
    if price_down and oi_up:
        return "Short Build-up"
    if price_up and oi_down:
        return "Short Covering"
    if price_down and oi_down:
        return "Long Unwinding"
    return "Neutral"


# ---------------------------------------------------------------------------
# PCR Engine
# ---------------------------------------------------------------------------

def overall_pcr(chain: list) -> float:
    """Put OI / Call OI across the whole chain. Returns 0.0 (neutral,
    not a crash) if there's no call OI at all yet (e.g. a brand new
    expiry) -- same fail-soft posture as scanner._broad_market_efficiency_ratio()."""
    calls = total_call_oi(chain)
    if calls <= 0:
        return 0.0
    return total_put_oi(chain) / calls


def strike_pcr(chain: list, strike: float) -> float:
    """PCR for one strike only -- put_oi/call_oi at that row. 0.0 if
    the strike isn't in the chain or has no call OI."""
    for row in chain:
        if row.get("strike_price") == strike:
            call_oi = _num(row.get("call_oi"))
            if call_oi <= 0:
                return 0.0
            return _num(row.get("put_oi")) / call_oi
    return 0.0


def intraday_pcr_change(current_pcr: float, previous_pcr) -> float:
    """Signed change vs. the last reading this process has seen for
    this symbol+expiry (previous_pcr may be None on the first read of
    a run -- see engine.py's per-process _pcr_history for who tracks
    that). Returns 0.0 with no prior reading, not a crash."""
    if previous_pcr is None:
        return 0.0
    return current_pcr - previous_pcr


def market_bias(pcr_value: float) -> str:
    if pcr_value <= PCR_BULLISH_MAX:
        return "Bullish"
    if pcr_value >= PCR_BEARISH_MIN:
        return "Bearish"
    return "Neutral"


def pcr_score(pcr_value: float, direction: str) -> float:
    """
    0-1 continuous strength: how strongly the OVERALL chain's PCR
    supports `direction` ("CE" wants Bullish/low-PCR, "PE" wants
    Bearish/high-PCR). Scaled linearly between the bullish and
    bearish config thresholds rather than a hard in/out band, so a
    PCR just short of the bearish threshold still gives a PE trade
    partial credit instead of zero.
    """
    span = PCR_BEARISH_MIN - PCR_BULLISH_MAX
    if span <= 0:
        return 0.5  # misconfigured thresholds -- neutral rather than divide-by-zero
    # 0.0 at PCR_BULLISH_MAX .. 1.0 at PCR_BEARISH_MIN, clamped
    normalized = (pcr_value - PCR_BULLISH_MAX) / span
    normalized = max(0.0, min(1.0, normalized))
    return normalized if direction == "PE" else (1.0 - normalized)


# ---------------------------------------------------------------------------
# Max Pain Engine
# ---------------------------------------------------------------------------

def max_pain_strike(chain: list):
    """
    Strike at which total option-writer payout (across every listed
    strike, both sides) is minimized -- the classic "max pain" level.
    Returns None if the chain has no usable OI data at all (rather
    than a misleading 0).
    """
    strikes = [row.get("strike_price") for row in chain if row.get("strike_price") is not None]
    if not strikes:
        return None

    best_strike = None
    best_payout = None
    for candidate in strikes:
        payout = 0.0
        for row in chain:
            k = row.get("strike_price")
            if k is None:
                continue
            call_oi = _num(row.get("call_oi"))
            put_oi = _num(row.get("put_oi"))
            if call_oi:
                payout += max(0.0, candidate - k) * call_oi
            if put_oi:
                payout += max(0.0, k - candidate) * put_oi
        if best_payout is None or payout < best_payout:
            best_payout = payout
            best_strike = candidate
    return best_strike


def max_pain_distance_pct(spot_price: float, max_pain: float) -> float:
    """Signed distance of spot from max pain, as a fraction of spot.
    Positive means spot is ABOVE max pain."""
    if not spot_price:
        return 0.0
    return (spot_price - max_pain) / spot_price


def max_pain_score(spot_price: float, max_pain, direction: str) -> float:
    """
    0-1: reads "movement toward max pain" as the market's usual pull,
    so a CE benefits from spot being BELOW max pain (room to drift up
    toward it) and a PE from spot being ABOVE it. Distance is scaled
    against MAX_PAIN_DISTANCE_CEILING_PCT the same way trade_score.py's
    ceiling-based strengths work -- full score once the gap is at
    least that wide, partial credit below it.
    """
    if max_pain is None or not spot_price:
        return 0.5  # no usable max-pain read -- neutral, not a penalty
    distance = max_pain_distance_pct(spot_price, max_pain)  # >0 = spot above max pain
    favorable_gap = distance if direction == "PE" else -distance
    return max(0.0, min(1.0, favorable_gap / MAX_PAIN_DISTANCE_CEILING_PCT)) if MAX_PAIN_DISTANCE_CEILING_PCT else 0.5


# ---------------------------------------------------------------------------
# Open Interest Engine
# ---------------------------------------------------------------------------

def oi_concentration(chain: list, direction: str, top_n: int = None) -> list:
    """
    Top-N strikes by OI on the relevant side (call_oi for CE, put_oi
    for PE) -- the strikes where the "smart money" wall is heaviest.
    Returns a list of (strike, oi) tuples, highest OI first.
    """
    top_n = top_n or OI_CONCENTRATION_TOP_N_STRIKES
    key = "call_oi" if direction == "CE" else "put_oi"
    rows = [(row.get("strike_price"), _num(row.get(key))) for row in chain if row.get("strike_price") is not None]
    rows.sort(key=lambda r: r[1], reverse=True)
    return rows[:top_n]


def oi_strength_score(oi: float, oi_change_pct_value: float, oi_concentration_rows: list, strike) -> float:
    """
    0-1: how strongly THIS strike's own OI picture supports being
    traded -- blends (a) OI build momentum since the last reading
    (reuses filters.oi_change_pct()'s output -- not recomputed here)
    and (b) whether this strike sits among the chain's own top-N OI
    concentration for its side (a strike inside a heavy-OI wall is a
    stronger institutional read than an isolated thin strike).
    """
    build_component = max(0.0, min(1.0, abs(oi_change_pct_value or 0.0) / (OI_BUILD_THRESHOLD_PCT * 2)))
    concentration_strikes = {row[0] for row in oi_concentration_rows}
    concentration_component = 1.0 if strike in concentration_strikes else 0.0
    return (build_component + concentration_component) / 2
