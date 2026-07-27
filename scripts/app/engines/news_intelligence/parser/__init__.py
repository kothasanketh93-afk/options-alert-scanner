"""
app/engines/news_intelligence/parser/ -- turns provider-raw dicts into
normalized NewsItem objects.

cleaner.py   text-level cleanup (HTML strip, whitespace, truncation)
normalizer.py per-provider field mapping -> NewsItem
parser.py    the single parse_batch() entry point collector.py calls
"""

from .parser import parse_batch, parse_one

__all__ = ["parse_batch", "parse_one"]
