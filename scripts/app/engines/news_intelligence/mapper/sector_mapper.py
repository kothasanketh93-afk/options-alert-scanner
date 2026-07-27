"""
app/engines/news_intelligence/mapper/sector_mapper.py -- symbol -> sector/
industry.
"""

from __future__ import annotations

# Coarse GICS-like sector per symbol -- good enough for impact-engine
# weighting and Telegram display; not a substitute for a full industry
# classification service.
_SYMBOL_SECTOR: dict[str, str] = {
    "RELIANCE": "Energy", "ONGC": "Energy", "BPCL": "Energy", "COALINDIA": "Energy",
    "NTPC": "Power", "POWERGRID": "Power",
    "TCS": "IT", "INFY": "IT", "WIPRO": "IT", "HCLTECH": "IT", "TECHM": "IT",
    "HDFCBANK": "Banking", "ICICIBANK": "Banking", "SBIN": "Banking",
    "KOTAKBANK": "Banking", "AXISBANK": "Banking", "INDUSINDBK": "Banking",
    "BAJFINANCE": "Financial Services", "BAJAJFINSV": "Financial Services",
    "SHRIRAMFIN": "Financial Services", "SBILIFE": "Insurance", "HDFCLIFE": "Insurance",
    "BHARTIARTL": "Telecom",
    "ITC": "FMCG", "HINDUNILVR": "FMCG", "NESTLEIND": "FMCG", "BRITANNIA": "FMCG",
    "MARUTI": "Automobile", "TATAMOTORS": "Automobile", "EICHERMOT": "Automobile",
    "HEROMOTOCO": "Automobile", "BAJAJ-AUTO": "Automobile", "M&M": "Automobile",
    "SUNPHARMA": "Pharma", "CIPLA": "Pharma", "DRREDDY": "Pharma", "DIVISLAB": "Pharma",
    "TATASTEEL": "Metals", "JSWSTEEL": "Metals",
    "LT": "Infrastructure", "ULTRACEMCO": "Cement", "GRASIM": "Cement",
    "ADANIENT": "Diversified", "ADANIPORTS": "Infrastructure",
    "ASIANPAINT": "Consumer Durables", "TITAN": "Consumer Durables",
    "APOLLOHOSP": "Healthcare",
}


def sector_for_symbol(symbol: str) -> str:
    return _SYMBOL_SECTOR.get(symbol.upper(), "Unclassified")


def sectors_for_symbols(symbols: list[str]) -> list[str]:
    seen = []
    for sym in symbols:
        sector = sector_for_symbol(sym)
        if sector not in seen:
            seen.append(sector)
    return seen
