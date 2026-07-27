"""
app/engines/news_intelligence/config/ -- YAML-driven settings for this
module only. Does NOT touch scripts/config.py.

get_settings() is the one function the rest of the package should call.
"""

from .loader import get_settings, reload_settings, NewsIntelligenceSettings

__all__ = ["get_settings", "reload_settings", "NewsIntelligenceSettings"]
