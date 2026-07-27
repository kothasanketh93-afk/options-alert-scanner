"""
app/engines/news_intelligence/mapper/symbol_mapper.py -- company name <->
NSE symbol lookup.
------------------------------------------------------------------------------
Ships with a small, self-contained seed table (large-cap NIFTY 50 /
BANKNIFTY constituents + a few common aliases) so this module works
completely standalone, per the "MUST be completely independent... Do NOT
integrate with the scanner" requirement -- it deliberately does NOT import
scripts/instrument_master.py (which pulls in the live broker instrument
dump and, transitively, angelone.py/upstox.py's broker-credential
requirements, the same coupling options_intelligence/engine.py's
_days_to_expiry_safe() docstring explains avoiding).

integration_note: once this module is wired in (see docs/INTEGRATION_GUIDE.md),
`load_symbol_table()` is the one function to point at the project's live
instrument master cache instead of SEED_TABLE, without touching any other
file in this package.
"""

from __future__ import annotations

import difflib
import re
from typing import NamedTuple, Optional

# symbol -> (company display name, tuple of extra aliases the mapper should
# also match, e.g. old/short names commonly used in headlines)
SEED_TABLE: dict[str, tuple[str, tuple[str, ...]]] = {
    "RELIANCE": ("Reliance Industries", ("Reliance Industries Limited", "RIL")),
    "TCS": ("Tata Consultancy Services", ("TCS Ltd",)),
    "HDFCBANK": ("HDFC Bank", ("HDFC Bank Limited",)),
    "ICICIBANK": ("ICICI Bank", ("ICICI Bank Limited",)),
    "INFY": ("Infosys", ("Infosys Limited", "Infosys Ltd")),
    "SBIN": ("State Bank of India", ("SBI",)),
    "BHARTIARTL": ("Bharti Airtel", ("Airtel",)),
    "ITC": ("ITC Limited", ("ITC Ltd",)),
    "KOTAKBANK": ("Kotak Mahindra Bank", ("Kotak Bank",)),
    "LT": ("Larsen & Toubro", ("L&T", "Larsen and Toubro")),
    "AXISBANK": ("Axis Bank", ("Axis Bank Limited",)),
    "BAJFINANCE": ("Bajaj Finance", ("Bajaj Finance Limited",)),
    "HINDUNILVR": ("Hindustan Unilever", ("HUL",)),
    "MARUTI": ("Maruti Suzuki", ("Maruti Suzuki India",)),
    "SUNPHARMA": ("Sun Pharmaceutical", ("Sun Pharma",)),
    "TATAMOTORS": ("Tata Motors", ("Tata Motors Limited",)),
    "TATASTEEL": ("Tata Steel", ("Tata Steel Limited",)),
    "WIPRO": ("Wipro", ("Wipro Limited",)),
    "ADANIENT": ("Adani Enterprises", ("Adani Enterprises Limited",)),
    "ADANIPORTS": ("Adani Ports", ("Adani Ports and SEZ",)),
    "ASIANPAINT": ("Asian Paints", ("Asian Paints Limited",)),
    "ULTRACEMCO": ("UltraTech Cement", ("UltraTech",)),
    "NTPC": ("NTPC Limited", ("NTPC",)),
    "POWERGRID": ("Power Grid Corporation", ("Power Grid",)),
    "ONGC": ("Oil and Natural Gas Corporation", ("ONGC",)),
    "COALINDIA": ("Coal India", ("Coal India Limited",)),
    "JSWSTEEL": ("JSW Steel", ("JSW Steel Limited",)),
    "HCLTECH": ("HCL Technologies", ("HCL Tech",)),
    "TECHM": ("Tech Mahindra", ("Tech Mahindra Limited",)),
    "TITAN": ("Titan Company", ("Titan",)),
    "BAJAJFINSV": ("Bajaj Finserv", ("Bajaj Finserv Limited",)),
    "INDUSINDBK": ("IndusInd Bank", ("IndusInd Bank Limited",)),
    "NESTLEIND": ("Nestle India", ("Nestle",)),
    "GRASIM": ("Grasim Industries", ("Grasim",)),
    "CIPLA": ("Cipla", ("Cipla Limited",)),
    "DRREDDY": ("Dr Reddys Laboratories", ("Dr. Reddy's",)),
    "EICHERMOT": ("Eicher Motors", ("Eicher Motors Limited",)),
    "HEROMOTOCO": ("Hero MotoCorp", ("Hero MotoCorp Limited",)),
    "BPCL": ("Bharat Petroleum", ("BPCL",)),
    "DIVISLAB": ("Divis Laboratories", ("Divi's Labs",)),
    "SBILIFE": ("SBI Life Insurance", ("SBI Life",)),
    "HDFCLIFE": ("HDFC Life Insurance", ("HDFC Life",)),
    "BRITANNIA": ("Britannia Industries", ("Britannia",)),
    "SHRIRAMFIN": ("Shriram Finance", ("Shriram Finance Limited",)),
    "APOLLOHOSP": ("Apollo Hospitals", ("Apollo Hospitals Enterprise",)),
    "BAJAJ-AUTO": ("Bajaj Auto", ("Bajaj Auto Limited",)),
    "M&M": ("Mahindra & Mahindra", ("Mahindra and Mahindra",)),
}


class SymbolMatch(NamedTuple):
    symbol: str
    company_name: str
    confidence: float   # 0..1


def load_symbol_table() -> dict[str, tuple[str, tuple[str, ...]]]:
    """Single override point -- see module docstring's integration_note."""
    return SEED_TABLE


def _all_names(symbol: str, entry: tuple[str, tuple[str, ...]]) -> list[str]:
    display_name, aliases = entry
    return [symbol, display_name, *aliases]


_GENERIC_SUFFIX_WORDS = {
    "industries", "limited", "ltd", "bank", "company", "corporation",
    "enterprises", "group", "india", "and", "the", "of", "consultancy",
    "services", "motors", "steel", "power", "finance", "insurance",
}


def find_symbols_in_text(text: str, min_confidence: float = 0.5,
                          fuzzy_threshold: float = 0.82) -> list[SymbolMatch]:
    """Exact/substring match first (fast, confidence 1.0), then a fuzzy
    difflib pass over remaining unmatched table entries for near-misses
    (OCR-ish/abbreviated headlines) at a lower confidence."""
    if not text:
        return []
    lowered = text.lower()
    table = load_symbol_table()
    matches: dict[str, SymbolMatch] = {}

    for symbol, entry in table.items():
        for name in _all_names(symbol, entry):
            # Word-boundary match, not raw substring -- a raw `in` check
            # would let a short symbol like "LT" false-positive-match
            # inside an unrelated word such as "multiple". \b doesn't
            # treat "&" as a word character, so "M&M" needs an explicit
            # escaped-literal pattern rather than relying on \b around it.
            pattern = r"(?<![A-Za-z0-9])" + re.escape(name.lower()) + r"(?![A-Za-z0-9])"
            if re.search(pattern, lowered):
                matches[symbol] = SymbolMatch(symbol, entry[0], 1.0)
                break

    if len(matches) < 5:  # only bother with the fuzzy pass on sparse exact-hit results
        words = [w for w in lowered.replace(",", " ").split() if len(w) > 3]
        for symbol, entry in table.items():
            if symbol in matches:
                continue
            display_name = entry[0]
            distinctive_tokens = [
                t for t in display_name.split() if t.lower() not in _GENERIC_SUFFIX_WORDS
            ]
            if len(distinctive_tokens) < 2:
                # A single distinctive token (e.g. "Tata" shared across Tata
                # Motors/Tata Steel/TCS, or a too-short token like "SBI")
                # is too ambiguous to fuzzy-match on alone -- exact matching
                # above already covers the unambiguous single-word case.
                continue

            def _ratio(word: str, token: str) -> float:
                # Guard against difflib inflating similarity for a short
                # token that's simply a substring of a longer, unrelated
                # word (e.g. "ports" inside "reports") -- require the same
                # first letter and a close length before trusting the ratio.
                if word[0] != token[0].lower() or abs(len(word) - len(token)) > 2:
                    return 0.0
                return difflib.SequenceMatcher(None, word, token.lower()).ratio()

            per_token_best = [
                max((_ratio(w, token) for w in words), default=0.0)
                for token in distinctive_tokens
            ]
            strong_hits = sum(1 for r in per_token_best if r >= fuzzy_threshold)
            # Require at least TWO distinctive tokens to independently
            # clear the threshold (e.g. both "SBI" AND "Life" found in
            # the text) -- a single short/ambiguous token alone (like
            # "SBI" fuzzy-matching unrelated word "sebi") is not enough
            # evidence on its own.
            if strong_hits < 2:
                continue
            best_ratio = min(r for r in per_token_best if r >= fuzzy_threshold)
            if best_ratio >= fuzzy_threshold:
                matches[symbol] = SymbolMatch(symbol, display_name, round(best_ratio, 3))

    return [m for m in matches.values() if m.confidence >= min_confidence]
