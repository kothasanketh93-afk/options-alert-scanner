"""
opportunity_score.py -- Phase 1: Opportunity Score Engine.
------------------------------------------------------------------------------
Scores every symbol in universe.MASTER_UNIVERSE (~100 stocks) on a
0-100 Opportunity Score using ONLY data available before market open,
and keeps the top config.TOP_N_STOCKS for detailed intraday scanning.
This is what makes scanning ~100 stocks affordable within roughly the
current GitHub Actions runtime budget -- scanner.detect_all_setups()
never touches the other ~65, all day.

WEIGHTS AND FORMULA: see config.OPPORTUNITY_SCORE_WEIGHTS and the
OPP_SCORE_*_CEILING constants next to it -- every ceiling/weight lives
in config.py, tunable without touching this file, same convention as
ENTRY_SCORE/STOCK_MIN_OI.

HONEST LIMITATION -- Relative Volume: the spec's original "Relative
Volume" input means today's volume-so-far vs. the 20-day average AT
THE SAME TIME OF DAY -- that's a genuinely intraday signal and cannot
exist before the market opens (today's volume is exactly zero at
08:45 IST). What's scored here as "rvol" is a documented proxy: was
YESTERDAY's full-day volume unusually high relative to its own 20-day
trailing average -- i.e. "did this stock just have an unusually active
session", a legitimate leading signal for continued interest today,
but NOT the same measurement the spec's wording literally describes.
Flagging this explicitly rather than silently mislabeling it.

CALLED FROM: build_opportunity_shortlist.py, once daily, premarket.
"""

from datetime import datetime, timezone, timedelta

import market_data
import instrument_master
import universe
from config import (
    OPPORTUNITY_SCORE_WEIGHTS, OPP_SCORE_RVOL_CEILING, OPP_SCORE_GAP_CEILING,
    OPP_SCORE_ATR_CEILING, OPP_SCORE_MOM_CEILING, OPP_SCORE_OI_CEILING,
    OPP_SCORE_LIQUIDITY_CEILING, OPP_SCORE_VWAP_CEILING, OPP_SCORE_RANGE_CEILING,
    TOP_N_STOCKS,
)
from daily_cache import DailyCache
from logging_setup import get_logger

IST = timezone(timedelta(hours=5, minutes=30))
_logger = get_logger("opportunity_score")

_DAILY_LOOKBACK = 25  # trading days of history requested -- covers the 20-day averages plus buffer for holidays


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _average_true_range(candles: list, period: int = 14) -> float:
    if len(candles) < period + 1:
        return 0.0
    true_ranges = []
    for i in range(1, len(candles)):
        high, low, prev_close = candles[i]["high"], candles[i]["low"], candles[i - 1]["close"]
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        true_ranges.append(tr)
    return sum(true_ranges[-period:]) / period


def _resolve_instrument_key(symbol: str, trading_date: str) -> str:
    """
    Reuses instrument_master's cached rows to find an EQ-segment
    instrument_key for `symbol` -- daily candles and batch quotes both
    need the equity instrument, not an option/future contract.
    """
    rows = instrument_master.load_instrument_master(trading_date)
    for row in rows:
        if row.get("underlying_symbol") == symbol and row.get("contract_class") == "EQUITY":
            return row.get("instrument_key")
    return None


def compute_symbol_inputs(symbol: str, trading_date: str, batch_quotes: dict,
                           instrument_key: str, prior_day_payload: dict) -> dict:
    """
    Gathers every raw input compute_opportunity_score() needs for one
    symbol. Returns None (logged) if there isn't enough data to score
    this symbol today -- same "skip, log why, keep the rest of the
    run alive" convention as instrument_master.validate_symbol().
    """
    quote = batch_quotes.get(instrument_key)
    if not quote:
        _logger.info(f"SKIP {symbol}: no batch quote returned for {instrument_key}")
        return None

    try:
        daily = market_data.get_recent_daily_candles(instrument_key, _DAILY_LOOKBACK)
    except Exception as exc:
        _logger.info(f"SKIP {symbol}: daily candle fetch failed ({type(exc).__name__}: {exc})")
        return None

    if len(daily) < 6:
        _logger.info(f"SKIP {symbol}: only {len(daily)} daily candles available, need at least 6")
        return None

    prev = daily[-1]
    prev_close = prev["close"]
    if not prev_close:
        _logger.info(f"SKIP {symbol}: previous close is zero/missing")
        return None

    prior_volumes = [c["volume"] for c in daily[-21:-1]] or [0]
    avg_volume_20d = sum(prior_volumes) / len(prior_volumes) if prior_volumes else 0
    rvol = (prev["volume"] / avg_volume_20d) if avg_volume_20d else 0.0

    ltp = quote.get("ltp") or prev_close
    gap_pct = (ltp - prev_close) / prev_close

    atr = _average_true_range(daily)
    atr_pct = atr / prev_close if prev_close else 0.0

    mom_5d = 0.0
    if len(daily) >= 6:
        close_5_ago = daily[-6]["close"]
        if close_5_ago:
            mom_5d = (prev_close - close_5_ago) / close_5_ago

    prev_range_pct = (prev["high"] - prev["low"]) / prev_close if prev_close else 0.0

    # VWAP distance approximation: daily candles have no intraday VWAP,
    # so this uses the typical-price approximation (H+L+C)/3 as a
    # stand-in for yesterday's VWAP -- documented approximation, not
    # a real intraday VWAP reconstruction.
    typical_price = (prev["high"] + prev["low"] + prev["close"]) / 3
    vwap_dist_pct = abs(prev_close - typical_price) / prev_close if prev_close else 0.0

    trade_values = [c["close"] * c["volume"] for c in daily[-20:]]
    adtv = sum(trade_values) / len(trade_values) if trade_values else 0.0

    total_oi = _safe_total_oi(symbol, trading_date)
    oi_chg_pct = 0.0
    if prior_day_payload and total_oi:
        prior_row = next(
            (r for r in prior_day_payload.get("all_scored", []) if r["symbol"] == symbol), None
        )
        if prior_row and prior_row.get("total_oi"):
            oi_chg_pct = (total_oi - prior_row["total_oi"]) / prior_row["total_oi"]

    return {
        "symbol": symbol, "sector": universe.sector_of(symbol),
        "rvol": rvol, "gap_pct": gap_pct, "atr_pct": atr_pct, "mom_5d": mom_5d,
        "oi_chg_pct": oi_chg_pct, "adtv": adtv, "vwap_dist_pct": vwap_dist_pct,
        "prev_range_pct": prev_range_pct, "total_oi": total_oi,
    }


def _safe_total_oi(symbol: str, trading_date: str) -> float:
    """
    Sums call_oi+put_oi across the nearest-expiry chain -- the ONE
    genuinely expensive-per-symbol call in this whole module (one
    option-chain fetch per universe symbol, ~100 calls once/day). Kept
    isolated in its own function, wrapped in try/except, so a broker
    error scoring OI for one symbol never blocks the other 8 factors
    for that symbol (returns None -> oi_chg_pct falls back to 0.0,
    i.e. neutral, not a crash).
    """
    try:
        ok, _ = instrument_master.validate_symbol(symbol, trading_date)
        if not ok:
            return None
        from option_chain import resolve_underlying_key, current_expiry
        underlying_key = resolve_underlying_key(symbol)
        expiry = current_expiry(symbol)
        chain = market_data.get_broker().get_option_chain(underlying_key, expiry)
        return sum((row.get("call_oi") or 0) + (row.get("put_oi") or 0) for row in chain)
    except Exception as exc:
        _logger.info(f"{symbol}: OI lookup failed ({type(exc).__name__}: {exc}) -- oi_change scored as neutral")
        return None


def compute_opportunity_score(inputs: dict, sector_ranks: dict) -> int:
    w = OPPORTUNITY_SCORE_WEIGHTS
    parts = {
        "rvol":       _clamp01(inputs["rvol"] / OPP_SCORE_RVOL_CEILING) * w["rvol"],
        "gap":        _clamp01(abs(inputs["gap_pct"]) / OPP_SCORE_GAP_CEILING) * w["gap"],
        "atr":        _clamp01(inputs["atr_pct"] / OPP_SCORE_ATR_CEILING) * w["atr"],
        "momentum":   _clamp01(abs(inputs["mom_5d"]) / OPP_SCORE_MOM_CEILING) * w["momentum"],
        "oi_change":  _clamp01(abs(inputs["oi_chg_pct"]) / OPP_SCORE_OI_CEILING) * w["oi_change"],
        "sector":     sector_ranks.get(inputs["sector"], 0.0) * w["sector"],
        "liquidity":  _clamp01(inputs["adtv"] / OPP_SCORE_LIQUIDITY_CEILING) * w["liquidity"],
        "vwap_dist":  _clamp01(inputs["vwap_dist_pct"] / OPP_SCORE_VWAP_CEILING) * w["vwap_dist"],
        "prev_range": _clamp01(inputs["prev_range_pct"] / OPP_SCORE_RANGE_CEILING) * w["prev_range"],
    }
    return round(sum(parts.values()))


def rank_sectors(all_inputs: list) -> dict:
    """
    Percentile-ranks each sector present today by its members' average
    5-day momentum -- "Sector Strength" per the spec. Returns
    {sector: 0..1}. A sector with only one or two members today is
    still ranked (no minimum-membership cutoff) -- with ~100 stocks
    across ~18 sectors, undersized sectors are the exception, not
    the rule, and excluding them would just zero out their members'
    sector-strength contribution instead, which isn't obviously better.
    """
    by_sector = {}
    for row in all_inputs:
        by_sector.setdefault(row["sector"], []).append(row["mom_5d"])
    avg_by_sector = {sector: sum(moms) / len(moms) for sector, moms in by_sector.items()}
    ordered = sorted(avg_by_sector.items(), key=lambda kv: kv[1])
    n = len(ordered)
    return {sector: (rank / (n - 1) if n > 1 else 1.0) for rank, (sector, _) in enumerate(ordered)}


def build_shortlist(trading_date: str = None) -> dict:
    """
    Full Phase 1 pipeline: batch-fetch quotes for the whole universe
    (ONE call, see broker.Broker.get_batch_quotes()'s docstring for
    why this matters), fetch per-symbol daily history + OI, score, and
    keep the top TOP_N_STOCKS. Returns the payload shape
    build_opportunity_shortlist.py caches via daily_cache.py:

      {"trading_date": ..., "shortlist": [{"symbol", "score"}, ...],
       "all_scored": [{"symbol", "score", "total_oi"}, ...]}

    "all_scored" (every universe symbol, not just the shortlist) is
    kept specifically so TOMORROW's run can read it back as
    prior_day_payload for oi_chg_pct -- see compute_symbol_inputs().
    """
    trading_date = trading_date or instrument_master.today_str()
    symbols = universe.universe_symbols()

    instrument_keys = {}
    for symbol in symbols:
        key = _resolve_instrument_key(symbol, trading_date)
        if key:
            instrument_keys[symbol] = key
        else:
            _logger.info(f"SKIP {symbol}: no EQUITY instrument_key found in today's instrument master")

    batch_quotes = market_data.get_broker().get_batch_quotes(list(instrument_keys.values()))

    cache = DailyCache("opportunity")
    yesterday = (datetime.strptime(trading_date, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    prior_day_payload = cache.read(yesterday)

    all_inputs = []
    for symbol, instrument_key in instrument_keys.items():
        inputs = compute_symbol_inputs(symbol, trading_date, batch_quotes, instrument_key, prior_day_payload)
        if inputs is not None:
            all_inputs.append(inputs)

    sector_ranks = rank_sectors(all_inputs)
    scored = [
        {"symbol": row["symbol"], "score": compute_opportunity_score(row, sector_ranks),
         "total_oi": row["total_oi"]}
        for row in all_inputs
    ]
    scored.sort(key=lambda r: r["score"], reverse=True)
    shortlist = scored[:TOP_N_STOCKS]

    _logger.info(
        f"Opportunity scoring: {len(symbols)} in universe, {len(all_inputs)} scored, "
        f"top {len(shortlist)} shortlisted. Top 10: "
        + ", ".join(f"{r['symbol']}={r['score']}" for r in scored[:10])
    )

    return {"trading_date": trading_date, "shortlist": shortlist, "all_scored": scored}
