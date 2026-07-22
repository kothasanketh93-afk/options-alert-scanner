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
    {"symbol": "LTIM", "sector": "IT"},
    {"symbol": "PERSISTENT", "sector": "IT"},
    {"symbol": "COFORGE", "sector": "IT"},
    {"symbol": "MPHASIS", "sector": "IT"},

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
    {"symbol": "TORNTPOWER", "sector": "POWER"},

    # --- Auto / Auto Ancillaries ---
    {"symbol": "MARUTI", "sector": "AUTO"},
    {"symbol": "TATAMOTORS", "sector": "AUTO"},
    {"symbol": "M&M", "sector": "AUTO"},
    {"symbol": "BAJAJ-AUTO", "sector": "AUTO"},
    {"symbol": "EICHERMOT", "sector": "AUTO"},
    {"symbol": "HEROMOTOCO", "sector": "AUTO"},
    {"symbol": "ASHOKLEY", "sector": "AUTO"},
    {"symbol": "MOTHERSON", "sector": "AUTO"},
    {"symbol": "TVSMOTOR", "sector": "AUTO"},
    {"symbol": "BOSCHLTD", "sector": "AUTO"},

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
    {"symbol": "ACC", "sector": "CEMENT"},
    {"symbol": "DALBHARAT", "sector": "CEMENT"},

    # --- Infrastructure / Capital Goods / Engineering ---
    {"symbol": "LT", "sector": "INFRA"},
    {"symbol": "BHEL", "sector": "INFRA"},
    {"symbol": "SIEMENS", "sector": "INFRA"},
    {"symbol": "ABB", "sector": "INFRA"},
    {"symbol": "CUMMINSIND", "sector": "INFRA"},
    {"symbol": "GMRINFRA", "sector": "INFRA"},
    {"symbol": "IRCTC", "sector": "INFRA"},
    {"symbol": "CONCOR", "sector": "INFRA"},

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

    # --- Telecom ---
    {"symbol": "BHARTIARTL", "sector": "TELECOM"},
    {"symbol": "IDEA", "sector": "TELECOM"},
    {"symbol": "INDUSTOWER", "sector": "TELECOM"},

    # --- Media / Realty / Diversified ---
    {"symbol": "ZEEL", "sector": "MEDIA"},
    {"symbol": "SUNTV", "sector": "MEDIA"},
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


def sector_of(symbol: str) -> str:
    for row in MASTER_UNIVERSE:
        if row["symbol"] == symbol:
            return row["sector"]
    return "UNKNOWN"


def all_sectors() -> list:
    return sorted({row["sector"] for row in MASTER_UNIVERSE})
