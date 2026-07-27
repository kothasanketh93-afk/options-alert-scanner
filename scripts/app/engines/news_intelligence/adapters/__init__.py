"""
app/engines/news_intelligence/adapters/ -- the boundary layer for future
Decision Engine integration. See decision_adapter.py's docstring: nothing
here imports or modifies scripts/app/engines/decision_engine/.
"""

from .decision_adapter import NewsDecisionAdapter, NewsScoreResult, get_news_score, default_adapter

__all__ = ["NewsDecisionAdapter", "NewsScoreResult", "get_news_score", "default_adapter"]
