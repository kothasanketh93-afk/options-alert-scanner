"""
app/engines/news_intelligence/models/ -- typed data contracts.
------------------------------------------------------------------------------
Dataclasses (not pydantic) for the same reason app/engines/early_breakout/
models.py chose TypedDict over dataclass for ITS boundary object: minimal
runtime surface, zero new mandatory dependency, and a shape every consumer
(cache, adapter, tests) can rely on without pulling pydantic into a module
that otherwise has zero third-party imports at the "core" layer. Pydantic
IS used, but only inside config/loader.py to validate settings.yaml at load
time (an edge, not the hot path) -- see that module's docstring.
"""

from .news import NewsItem, NewsCategory
from .sentiment import SentimentLabel, SentimentResult
from .impact import ImpactLevel, ImpactResult

__all__ = [
    "NewsItem",
    "NewsCategory",
    "SentimentLabel",
    "SentimentResult",
    "ImpactLevel",
    "ImpactResult",
]
