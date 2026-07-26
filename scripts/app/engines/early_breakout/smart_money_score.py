"""
app/engines/early_breakout/smart_money_score.py -- Smart Money Score.
------------------------------------------------------------------------------
HONEST SCOPE NOTE (read before trusting this): the spec's full wishlist
for this score -- "Large Bid Quantity", "Large Trades", "Delivery
Expansion", "Bid Dominance", "Offer Absorption" -- describes order-book
DEPTH (quantity at each price level) and trade-TAPE data (individual
print sizes) plus cash-market delivery %, none of which either broker
integration in this codebase exposes. option_chain._extract_leg()
returns best bid/ask PRICE, OI, volume, LTP, and delta -- no bid/ask
QUANTITY, no tape, no delivery data (delivery % is a cash-equity/
futures concept; it doesn't apply to options at all). Angel One has no
option-chain endpoint of its own (angelone.py's docstring) and Upstox's
quote response doesn't carry L2 depth for options either.

So this module computes a Smart Money Score from what's ACTUALLY
available, and is explicit in its detail dict about which requested
sub-factors are approximated vs. genuinely unavailable, rather than
fabricating numbers for signals the data can't support:
  - Volume spike           -- REAL (option volume vs. its own recent history)
  - OI build-up            -- REAL (oi_history, same source scanner.py's
                               engine already uses)
  - Delta magnitude         -- REAL (a rough proxy for "how much
                               directional conviction the flow implies",
                               not a substitute for actual large-trade
                               detection)
  - Bid-ask spread tightness -- APPROXIMATE proxy for "orderly, liquid
                               two-sided market" (tight spread), used
                               in place of the requested "Bid Dominance"/
                               "Offer Absorption" (which need
                               quantity-at-price data this codebase
                               doesn't have)
  - Large bid quantity, large trades, delivery expansion -- NOT
    AVAILABLE from either broker integration; contribute 0 and are
    flagged "unavailable" in the detail dict rather than guessed at.
"""

from config import OI_BUILD_THRESHOLD_PCT


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _volume_spike_score(volume, avg_volume, ceiling_mult: float = 3.0) -> float:
    if not avg_volume or volume is None:
        return 0.0
    return _clamp01((volume / avg_volume) / ceiling_mult)


def _oi_buildup_score(oi_change_pct_value) -> float:
    if oi_change_pct_value is None:
        return 0.0
    # full score at 3x the standard OI-build threshold already used
    # elsewhere in this codebase (config.OI_BUILD_THRESHOLD_PCT)
    return _clamp01(abs(oi_change_pct_value) / (OI_BUILD_THRESHOLD_PCT * 3))


def _delta_magnitude_score(delta, ceiling: float = 0.6) -> float:
    """Rough proxy only -- see module docstring. Higher |delta| = more
    directional conviction being taken on, up to `ceiling` (beyond
    which it's just a deep-ITM contract, not necessarily "smart
    money")."""
    if delta is None:
        return 0.0
    return _clamp01(abs(delta) / ceiling)


def _spread_tightness_score(bid, ask, premium, ceiling_pct: float = 0.03) -> float:
    if not bid or not ask or not premium or premium <= 0:
        return 0.0
    spread_pct = (ask - bid) / premium
    return _clamp01(1.0 - spread_pct / ceiling_pct)


def compute_smart_money_score(volume, avg_volume, oi_change_pct_value, delta, bid, ask, premium) -> dict:
    """
    Returns {"smart_money_score": 0-100 int, "detail": {...}}. Equal-
    weighted mean of the four REAL/approximate sub-factors above --
    the genuinely-unavailable ones are recorded as "unavailable" in
    detail, not averaged in as silent zeros that would look like a
    real "no smart money" reading.
    """
    volume_score = _volume_spike_score(volume, avg_volume)
    oi_score = _oi_buildup_score(oi_change_pct_value)
    delta_score = _delta_magnitude_score(delta)
    spread_score = _spread_tightness_score(bid, ask, premium)

    smart_money_score = round(100 * (volume_score + oi_score + delta_score + spread_score) / 4)

    return {
        "smart_money_score": smart_money_score,
        "detail": {
            "volume_spike": round(volume_score, 2),
            "oi_buildup": round(oi_score, 2),
            "delta_magnitude": round(delta_score, 2),
            "spread_tightness": round(spread_score, 2),
            "large_bid_quantity": "unavailable (no order-book depth from either broker)",
            "large_trades": "unavailable (no trade-tape data)",
            "delivery_expansion": "not applicable to options",
        },
    }
