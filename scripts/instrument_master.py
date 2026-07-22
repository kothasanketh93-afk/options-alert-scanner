"""
instrument_master.py -- Phase 2: Dynamic Strike Selection.
------------------------------------------------------------------------------
Replaces config.STRIKE_STEP / config.STOCK_STRIKE_STEP's hardcoded,
unverified-per-config.py's-own-comments strike intervals with a
data-driven engine: download the real instrument master once a day,
persist it across runs via daily_cache.py (state-branch git commit --
see that module's docstring for why this can't just be an in-process
cache), and read actual listed strikes off it instead of guessing a
spacing and doing arithmetic.

WHERE THIS PLUGS IN:
  - option_chain.get_atm_strike() calls nearest_atm_and_offsets() below
    instead of `round(spot / STRIKE_STEP[symbol]) * STRIKE_STEP[symbol]`.
  - scanner.moneyness_offsets() + the strike loop in scanner.detect_setups()
    index into the real strikes array (via nearest_atm_and_offsets())
    instead of multiplying an offset by a hardcoded step.
  - premarket.yml's new opportunity-shortlist step calls
    download_and_cache_instrument_master() once, before market open, so
    every later `trader.yml` run of the day reads the cached master
    instead of re-downloading a multi-MB file every 5 minutes.

FAILURE MODE: every function below returns None / (False, reason) on
missing data rather than raising past the caller -- same "skip this
symbol, log why, keep the rest of the run alive" convention
scanner.detect_all_setups() already uses. A symbol with bad or missing
strike data should never take down the whole scan.
"""

from datetime import datetime, timezone, timedelta

import market_data
from daily_cache import DailyCache
from logging_setup import get_logger

IST = timezone(timedelta(hours=5, minutes=30))
_logger = get_logger("instrument_master")

_CACHE = DailyCache("instrument_master", compressed=True)

# In-process only (this run's copy of today's already-cached master) --
# avoids re-reading/re-decompressing the cache file from disk on every
# one of the ~10-20 strike lookups a single run makes. Reset per
# process, same lifetime as market_data.py's broker singleton.
_inprocess_master = {}  # trading_date -> list[dict]


def today_str(now: datetime = None) -> str:
    now = now or datetime.now(IST)
    return now.strftime("%Y-%m-%d")


def download_and_cache_instrument_master(trading_date: str = None) -> list:
    """
    Downloads via market_data.get_broker().download_instrument_master()
    (already failover-wrapped, count_failures=False per
    broker_failover.py -- see that file's comment on why a data
    download failure shouldn't trip the primary/secondary switch), then
    persists it to the state branch via daily_cache.py so every later
    run TODAY reads the cache instead of downloading again.

    Called from build_opportunity_shortlist.py's premarket run. Also
    safe to call mid-day as a fallback (see load_instrument_master()
    below) -- an intraday run that finds no cache yet just pays the
    download cost once, the same run that would have paid it anyway
    under the OLD per-run TTLCache-only behavior.
    """
    trading_date = trading_date or today_str()
    rows = market_data.get_broker().download_instrument_master()
    payload = {"trading_date": trading_date, "row_count": len(rows), "rows": rows}
    ok = _CACHE.write(trading_date, payload,
                       commit_message=f"instrument_master: {trading_date} ({len(rows)} rows)")
    if not ok:
        _logger.error(
            f"Instrument master for {trading_date} downloaded ({len(rows)} rows) but "
            f"FAILED to persist to the state branch -- this run's in-process copy is "
            f"still usable, but later runs today will have to re-download."
        )
    _inprocess_master[trading_date] = rows
    return rows


def load_instrument_master(trading_date: str = None) -> list:
    """
    Single entry point every other function in this module goes
    through. Order: this run's own in-process copy -> today's cached
    copy from the state branch -> download fresh (and cache it) as a
    last resort. The last case means the FIRST run of a day that finds
    no cache yet (e.g. premarket.yml's step failed or hasn't run) pays
    the download cost itself rather than returning nothing -- same
    "degrade, don't go dark" posture as config.load_symbols_for_today()'s
    fallback watchlist.
    """
    trading_date = trading_date or today_str()
    if trading_date in _inprocess_master:
        return _inprocess_master[trading_date]

    cached = _CACHE.read(trading_date)
    if cached is not None:
        rows = cached.get("rows", [])
        _inprocess_master[trading_date] = rows
        return rows

    _logger.info(
        f"No cached instrument master for {trading_date} yet -- downloading now "
        f"(expected once/day from premarket.yml; this run is paying that cost "
        f"itself because the cache wasn't there when it looked)."
    )
    return download_and_cache_instrument_master(trading_date)


def validate_symbol(symbol: str, trading_date: str = None) -> tuple:
    """
    Returns (ok: bool, reason: str) -- reason is populated on BOTH
    success and failure so callers can log either without a second
    branch, matching the spec's "Reason for Skip" / "Reason for
    Success" requirement.
    """
    trading_date = trading_date or today_str()
    rows = load_instrument_master(trading_date)
    matches = [r for r in rows if r.get("underlying_symbol") == symbol or r.get("symbol") == symbol]
    if not matches:
        return False, f"{symbol}: not found in instrument master -- likely dropped from F&O this quarter"

    option_rows = [r for r in matches if r.get("contract_class") in ("INDEX_OPTION", "STOCK_OPTION")]
    if not option_rows:
        return False, f"{symbol}: found in instrument master but no option contracts -- not F&O eligible"

    unexpired = sorted({r["expiry"] for r in option_rows if r.get("expiry") and r["expiry"] >= trading_date})
    if not unexpired:
        return False, f"{symbol}: no unexpired option contracts listed"
    nearest_expiry = unexpired[0]

    lot_sizes = {r["lot_size"] for r in option_rows if r.get("expiry") == nearest_expiry and r.get("lot_size")}
    if not lot_sizes:
        return False, f"{symbol}: no valid lot size found for nearest expiry {nearest_expiry}"
    lot_size = sorted(lot_sizes)[0]

    strikes = strikes_for(symbol, nearest_expiry, trading_date)
    if len(strikes) < 5:
        return False, (f"{symbol}: only {len(strikes)} strikes listed for {nearest_expiry} "
                        f"-- too thin to safely trade ATM\u00b12")

    return True, (f"{symbol}: OK -- {len(strikes)} strikes, lot_size={lot_size}, "
                  f"expiry={nearest_expiry}")


def strikes_for(symbol: str, expiry: str, trading_date: str = None) -> list:
    """
    All listed strikes for symbol+expiry, sorted ascending. This is
    the ENTIRE replacement for config.STRIKE_STEP -- the spacing is
    whatever's actually listed, never assumed uniform, never hardcoded.
    """
    trading_date = trading_date or today_str()
    rows = load_instrument_master(trading_date)
    matches = [
        r for r in rows
        if (r.get("underlying_symbol") == symbol or r.get("symbol") == symbol)
        and r.get("expiry") == expiry
        and r.get("contract_class") in ("INDEX_OPTION", "STOCK_OPTION")
        and r.get("strike_price") is not None
    ]
    return sorted({r["strike_price"] for r in matches})


def nearest_atm_and_offsets(symbol: str, spot_price: float, n_each_side: int,
                             trading_date: str = None, expiry: str = None) -> dict:
    """
    Replaces the old `round(spot / STRIKE_STEP[symbol]) * STRIKE_STEP[symbol]`
    arithmetic, which assumes uniform strike spacing (true for
    NIFTY/BANKNIFTY, not reliably true for individual stock options --
    see config.py's own "unverified" caveats on STOCK_STRIKE_STEP).
    Finds the ATM strike by nearest-value lookup into the REAL sorted
    strikes array, then walks outward by array position (not by a
    fixed rupee step) for ITM/OTM offsets.

    Returns None if no strikes are available (caller should treat this
    exactly like a validate_symbol() failure -- skip the symbol, log
    why) or a dict:
        {"ATM": [strike], "ITM": [strike, ...], "OTM": [strike, ...]}
    ITM/OTM here are UNSIGNED walks toward lower/higher strikes
    respectively (array position -1, -2, ... and +1, +2, ...) --
    callers (scanner.moneyness_offsets()) are responsible for mapping
    "lower strike" / "higher strike" to ITM/OTM per CE-vs-PE direction,
    the same responsibility that function already has today.
    """
    trading_date = trading_date or today_str()
    if expiry is None:
        from option_chain import current_expiry
        expiry = current_expiry(symbol)

    strikes = strikes_for(symbol, expiry, trading_date)
    if not strikes:
        return None

    atm_index = min(range(len(strikes)), key=lambda i: abs(strikes[i] - spot_price))
    result = {"ATM": [strikes[atm_index]], "LOWER": [], "HIGHER": []}
    for offset in range(1, n_each_side + 1):
        if atm_index - offset >= 0:
            result["LOWER"].append(strikes[atm_index - offset])
        if atm_index + offset < len(strikes):
            result["HIGHER"].append(strikes[atm_index + offset])
    return result


def describe_strike_selection(symbol: str, trading_date: str = None) -> dict:
    """
    Matches the exact output shape from the Phase 2 spec (spot / ATM /
    ITM1 / ITM2 / OTM1 / OTM2) for a quick per-symbol sanity check --
    e.g. from a one-off script or premarket diagnostic, not called from
    the hot scan path. Assumes CE-style ordering (ITM = lower strikes)
    purely for this descriptive output; the real scan path's
    ITM/OTM mapping is direction-aware (see scanner.moneyness_offsets()).
    """
    from option_chain import get_atm_strike

    trading_date = trading_date or today_str()
    ok, reason = validate_symbol(symbol, trading_date)
    if not ok:
        _logger.info(f"SKIP {reason}")
        return None
    _logger.info(f"SUCCESS {reason}")

    atm_strike, spot_price = get_atm_strike(symbol)
    offsets = nearest_atm_and_offsets(symbol, spot_price, n_each_side=2, trading_date=trading_date)
    if offsets is None:
        _logger.info(f"SKIP {symbol}: passed validation but no strikes returned for the resolved expiry")
        return None

    return {
        "symbol": symbol,
        "spot": spot_price,
        "atm": atm_strike,
        "itm1": offsets["LOWER"][0] if len(offsets["LOWER"]) > 0 else None,
        "itm2": offsets["LOWER"][1] if len(offsets["LOWER"]) > 1 else None,
        "otm1": offsets["HIGHER"][0] if len(offsets["HIGHER"]) > 0 else None,
        "otm2": offsets["HIGHER"][1] if len(offsets["HIGHER"]) > 1 else None,
    }
