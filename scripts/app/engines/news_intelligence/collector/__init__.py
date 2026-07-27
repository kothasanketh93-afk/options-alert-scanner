"""
app/engines/news_intelligence/collector/ -- fetch -> parse -> map -> dedup
-> cache pipeline.
"""

from .collector import collect_all, CollectionResult
from .scheduler import NewsScheduler

__all__ = ["collect_all", "CollectionResult", "NewsScheduler"]
