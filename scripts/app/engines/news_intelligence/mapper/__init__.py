"""
app/engines/news_intelligence/mapper/ -- detects which company/symbol/
sector a NewsItem is about.

symbol_mapper.py  NSE symbol <-> company-name lookup table + fuzzy match
company_mapper.py  orchestrates detection over a NewsItem's text, returns
                    confidence
sector_mapper.py   symbol -> sector/industry lookup
"""

from .company_mapper import map_news_item, MappingResult

__all__ = ["map_news_item", "MappingResult"]
