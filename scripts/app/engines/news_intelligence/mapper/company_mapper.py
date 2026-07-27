"""
app/engines/news_intelligence/mapper/company_mapper.py -- orchestrates
symbol_mapper + sector_mapper over one NewsItem's text, mutating nothing --
returns a MappingResult the caller (engine.py) applies to its own copy of
the item.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..models.news import NewsItem
from . import sector_mapper, symbol_mapper


@dataclass
class MappingResult:
    companies: list[str] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)
    sectors: list[str] = field(default_factory=list)
    confidence: float = 0.0


def map_news_item(item: NewsItem, min_confidence: float = 0.5,
                   fuzzy_threshold: float = 0.82) -> MappingResult:
    text = f"{item.headline} {item.summary}"
    matches = symbol_mapper.find_symbols_in_text(
        text, min_confidence=min_confidence, fuzzy_threshold=fuzzy_threshold,
    )
    if not matches:
        return MappingResult()

    symbols = [m.symbol for m in matches]
    companies = [m.company_name for m in matches]
    sectors = sector_mapper.sectors_for_symbols(symbols)
    # Overall confidence: best single match drives it -- one strong hit
    # (e.g. an exact company name in the headline) shouldn't be diluted by
    # several weak fuzzy co-hits.
    confidence = max(m.confidence for m in matches)

    return MappingResult(companies=companies, symbols=symbols, sectors=sectors, confidence=confidence)
