"""
app/engines/news_intelligence/cache/ -- in-memory TTL cache for normalized
NewsItems. See news_cache.py's docstring for the design rationale.
"""

from .news_cache import NewsCache, get_default_cache

__all__ = ["NewsCache", "get_default_cache"]
