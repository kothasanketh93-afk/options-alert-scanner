"""
universe.py -- Phase 1: Master Universe.
------------------------------------------------------------------------------
The ~100-stock pool opportunity_score.py scores every morning before
picking the top config.TOP_N_STOCKS. Replaces config.FALLBACK_STOCK_
WATCHLIST (the old static 15-stock list) as the thing that actually
decides which stocks get scanned -- FALLBACK_STOCK_WATCHLIST still
exists, but only as the safety net for a day this scoring pipeline
doesn't run (see config.refresh_symbols_for_today()).

SAME VERIFICATION CAVEAT AS config.FALLBACK_STOCK_WATCHLIST /
STOCK_STRIKE_STEP: NSE revises F&O eligibility quarterly (SEBI's
liquidity-based inclusion/exclusion review). Review this list against
NSE's current F&O stock list every quarter -- a delisted or newly-
excluded name here just gets skipped by instrument_master.validate_symbol()
(logged, not fatal), but a name that's newly F&O-ELIGIBLE and NOT yet
in this list will never be scored or scanned no matter how good an
opportunity it is. Sector tags feed opportunity_score.py's Sector
Strength factor -- keep a name's sector reasonably current too (a
sector reclassification is much rarer than F&O eligibility changing,
but does happen, e.g. conglomerate spinoffs).

Deliberately a flat list of dicts, not nested by sector, so scoring
code doesn't need two different lookup paths (all-symbols vs.
by-sector) -- sector_of() below is the one place that groups by sector
when needed.
"""

MASTER_UNIVERSE = [
    # --- Banking / Financial Services ---
    {"symbol": "HDFCBANK", "sector": "BANKING"},
    {"symbol": "ICICIBANK", "sector": "BANKING"},
    {"symbol": "KOTAKBANK", "sector": "BANKING"},
    {"symbol": "AXISBANK", "sector": "BANKING"},
    {"symbol": "SBIN", "sector": "BANKING"},
    {"symbol": "INDUSINDBK", "sector": "BANKING"},
    {"symbol": "BANKBARODA", "sector": "BANKING"},
    {"symbol": "FEDERALBNK", "sector": "BANKING"},
    {"symbol": "IDFCFIRSTB", "sector": "BANKING"},
    {"symbol": "PNB", "sector": "BANKING"},
    {"symbol": "AUBANK", "sector": "BANKING"},

    # --- NBFC / Financial Services (non-bank) ---
    {"symbol": "BAJFINANCE", "sector": "FINANCE"},
    {"symbol": "BAJAJFINSV", "sector": "FINANCE"},
    {"symbol": "HDFCLIFE", "sector": "FINANCE"},
    {"symbol": "SBILIFE", "sector": "FINANCE"},
    {"symbol": "ICICIGI", "sector": "FINANCE"},
    {"symbol": "ICICIPRULI", "sector": "FINANCE"},
    {"symbol": "CHOLAFIN", "sector": "FINANCE"},
    {"symbol": "MUTHOOTFIN", "sector": "FINANCE"},
    {"symbol": "PFC", "sector": "FINANCE"},
    {"symbol": "RECLTD", "sector": "FINANCE"},
    {"symbol": "LICHSGFIN", "sector": "FINANCE"},

    # --- IT / Technology ---
    {"symbol": "TCS", "sector": "IT"},
    {"symbol": "INFY", "sector": "IT"},
    {"symbol": "HCLTECH", "sector": "IT"},
    {"symbol": "WIPRO", "sector": "IT"},
    {"symbol": "TECHM", "sector": "IT"},
    {"symbol": "PERSISTENT", "sector": "IT"},
    {"symbol": "COFORGE", "sector": "IT"},
    {"symbol": "MPHASIS", "sector": "IT"},
    # NOTE: 8 members, was 9 -- LTIM removed 2026-07-24 (F&O verification
    # failure), LTTS tried as replacement same day but ALSO failed live
    # F&O check (no CE/OPTSTK rows on either broker's master) -- left at
    # 8 rather than guess again with no way to verify from this session.

    # --- Energy / Oil & Gas ---
    {"symbol": "RELIANCE", "sector": "ENERGY"},
    {"symbol": "ONGC", "sector": "ENERGY"},
    {"symbol": "BPCL", "sector": "ENERGY"},
    {"symbol": "IOC", "sector": "ENERGY"},
    {"symbol": "GAIL", "sector": "ENERGY"},
    {"symbol": "PETRONET", "sector": "ENERGY"},
    {"symbol": "OIL", "sector": "ENERGY"},

    # --- Power / Utilities ---
    {"symbol": "NTPC", "sector": "POWER"},
    {"symbol": "POWERGRID", "sector": "POWER"},
    {"symbol": "TATAPOWER", "sector": "POWER"},
    {"symbol": "ADANIPOWER", "sector": "POWER"},
    {"symbol": "NHPC", "sector": "POWER"},
    {"symbol": "JSWENERGY", "sector": "POWER"},

    # --- Auto / Auto Ancillaries ---
    {"symbol": "MARUTI", "sector": "AUTO"},
    {"symbol": "M&M", "sector": "AUTO"},
    {"symbol": "BAJAJ-AUTO", "sector": "AUTO"},
    {"symbol": "EICHERMOT", "sector": "AUTO"},
    {"symbol": "HEROMOTOCO", "sector": "AUTO"},
    {"symbol": "ASHOKLEY", "sector": "AUTO"},
    {"symbol": "MOTHERSON", "sector": "AUTO"},
    {"symbol": "TVSMOTOR", "sector": "AUTO"},
    {"symbol": "BOSCHLTD", "sector": "AUTO"},
    {"symbol": "BHARATFORG", "sector": "AUTO"},

    # --- Metals & Mining ---
    {"symbol": "TATASTEEL", "sector": "METALS"},
    {"symbol": "JSWSTEEL", "sector": "METALS"},
    {"symbol": "HINDALCO", "sector": "METALS"},
    {"symbol": "VEDL", "sector": "METALS"},
    {"symbol": "JINDALSTEL", "sector": "METALS"},
    {"symbol": "SAIL", "sector": "METALS"},
    {"symbol": "NATIONALUM", "sector": "METALS"},
    {"symbol": "NMDC", "sector": "METALS"},
    {"symbol": "HINDZINC", "sector": "METALS"},

    # --- Cement / Construction Materials ---
    {"symbol": "ULTRACEMCO", "sector": "CEMENT"},
    {"symbol": "SHREECEM", "sector": "CEMENT"},
    {"symbol": "AMBUJACEM", "sector": "CEMENT"},
    {"symbol": "DALBHARAT", "sector": "CEMENT"},
    # NOTE: 4 members, was 5 -- ACC removed 2026-07-24 (F&O verification
    # failure), JKCEMENT tried as replacement same day but ALSO failed
    # live F&O check (no CE/OPTSTK rows on either broker's master) --
    # left at 4 rather than guess again with no way to verify from this
    # session.

    # --- Infrastructure / Capital Goods / Engineering ---
    {"symbol": "LT", "sector": "INFRA"},
    {"symbol": "BHEL", "sector": "INFRA"},
    {"symbol": "SIEMENS", "sector": "INFRA"},
    {"symbol": "ABB", "sector": "INFRA"},
    {"symbol": "CUMMINSIND", "sector": "INFRA"},
    {"symbol": "CONCOR", "sector": "INFRA"},
    {"symbol": "POLYCAB", "sector": "INFRA"},

    # --- Pharma / Healthcare ---
    {"symbol": "SUNPHARMA", "sector": "PHARMA"},
    {"symbol": "DRREDDY", "sector": "PHARMA"},
    {"symbol": "CIPLA", "sector": "PHARMA"},
    {"symbol": "DIVISLAB", "sector": "PHARMA"},
    {"symbol": "AUROPHARMA", "sector": "PHARMA"},
    {"symbol": "LUPIN", "sector": "PHARMA"},
    {"symbol": "BIOCON", "sector": "PHARMA"},
    {"symbol": "APOLLOHOSP", "sector": "PHARMA"},
    {"symbol": "MAXHEALTH", "sector": "PHARMA"},

    # --- FMCG / Consumer ---
    {"symbol": "HINDUNILVR", "sector": "FMCG"},
    {"symbol": "ITC", "sector": "FMCG"},
    {"symbol": "NESTLEIND", "sector": "FMCG"},
    {"symbol": "BRITANNIA", "sector": "FMCG"},
    {"symbol": "TATACONSUM", "sector": "FMCG"},
    {"symbol": "DABUR", "sector": "FMCG"},
    {"symbol": "MARICO", "sector": "FMCG"},
    {"symbol": "COLPAL", "sector": "FMCG"},
    {"symbol": "GODREJCP", "sector": "FMCG"},

    # --- Retail / Consumer Discretionary ---
    {"symbol": "TITAN", "sector": "CONSUMER"},
    {"symbol": "TRENT", "sector": "CONSUMER"},
    {"symbol": "DMART", "sector": "CONSUMER"},
    {"symbol": "PAGEIND", "sector": "CONSUMER"},
    {"symbol": "ASIANPAINT", "sector": "CONSUMER"},
    {"symbol": "DIXON", "sector": "CONSUMER"},
    {"symbol": "HAVELLS", "sector": "CONSUMER"},

    # --- Telecom ---
    {"symbol": "BHARTIARTL", "sector": "TELECOM"},
    {"symbol": "IDEA", "sector": "TELECOM"},
    {"symbol": "INDUSTOWER", "sector": "TELECOM"},

    # --- Realty / Diversified ---
    # (ZEEL/SUNTV removed 2026-07-24 -- F&O verification failure; no other
    # liquid F&O-eligible MEDIA name to replace them with, so their two
    # slots went to CONSUMER above (DIXON, HAVELLS) instead of leaving a
    # singleton MEDIA sector, which would skew opportunity_score.py's
    # per-sector percentile ranking.)
    {"symbol": "DLF", "sector": "REALTY"},
    {"symbol": "OBEROIRLTY", "sector": "REALTY"},
    {"symbol": "LODHA", "sector": "REALTY"},
    {"symbol": "ADANIENT", "sector": "DIVERSIFIED"},
    {"symbol": "ADANIPORTS", "sector": "DIVERSIFIED"},

    # --- Defence (selected small/mid cap per spec's explicit ask) ---
    {"symbol": "HAL", "sector": "DEFENCE"},
    {"symbol": "BEL", "sector": "DEFENCE"},
]


def universe_symbols() -> list:
    return [row["symbol"] for row in MASTER_UNIVERSE]


def fno_eligible_stock_symbols(trading_date: str = None) -> list:
    """
    Phase 5 (2026-07-24): the REAL, exchange-wide F&O-eligible stock
    list for `trading_date`, read straight from the broker's instrument
    master (contract_class == "STOCK_OPTION") instead of the ~112-name
    hand-curated MASTER_UNIVERSE above. NSE revises F&O eligibility
    every quarter (SEBI's liquidity-based review) -- reading this from
    the instrument master means that revision is picked up automatically
    the next time premarket.yml refreshes the cache, with no manual
    edit to this file ever needed. Currently runs to roughly 200-250
    symbols exchange-wide (NOT thousands -- India's F&O segment is a
    curated liquid subset of all NSE-listed stocks, so "top 500" isn't
    achievable here regardless of scoring; see README's Phase 5 note).

    Returns [] (not an exception) on any lookup problem -- callers
    (opportunity_score.build_shortlist()) fall back to the static
    MASTER_UNIVERSE via universe_symbols() in that case, same
    "degrade, don't go dark" posture as config.refresh_symbols_for_today().
    Index underlyings (NIFTY/BANKNIFTY/...) are excluded -- this is a
    STOCK list, indices are handled separately via config.SYMBOLS.
    """
    try:
        import instrument_master
        import config as _config
        rows = instrument_master.load_instrument_master(trading_date)
    except Exception as exc:
        _logger = __import__("logging_setup").get_logger("universe")
        _logger.warning(
            f"fno_eligible_stock_symbols: instrument master lookup failed "
            f"({type(exc).__name__}: {exc}) -- falling back to MASTER_UNIVERSE",
            exc_info=True,
        )
        return []

    symbols = {
        row.get("underlying_symbol")
        for row in rows
        if row.get("contract_class") == "STOCK_OPTION" and row.get("underlying_symbol")
    }
    symbols -= set(_config.INDEX_SYMBOLS)
    return sorted(symbols)


def sector_of(symbol: str) -> str:
    for row in MASTER_UNIVERSE:
        if row["symbol"] == symbol:
            return row["sector"]
    return "UNKNOWN"


def all_sectors() -> list:
    return sorted({row["sector"] for row in MASTER_UNIVERSE})