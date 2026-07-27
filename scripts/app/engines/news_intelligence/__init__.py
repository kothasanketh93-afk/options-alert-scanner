"""
app/engines/news_intelligence/ -- Part 11.5: News Intelligence Engine.
------------------------------------------------------------------------------
Completely independent module. Does NOT modify, import for side effects,
or get imported by the scanner, decision engine, options intelligence,
monitoring, health services, dashboard, telegram, or any existing config.

Public Python API:

    from app.engines.news_intelligence import NewsEngine

    engine = NewsEngine()
    engine.collect()
    engine.get_news_score("RELIANCE")

See README.md for the full architecture, and docs/INTEGRATION_GUIDE.md for
how a future change can wire adapters/decision_adapter.py into the actual
Decision Engine.
"""

from .engine import NewsEngine

__all__ = ["NewsEngine"]
