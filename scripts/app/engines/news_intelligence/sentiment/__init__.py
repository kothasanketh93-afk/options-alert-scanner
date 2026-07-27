"""
app/engines/news_intelligence/sentiment/ -- local, lexicon-based sentiment
analysis. No OpenAI/Claude/cloud AI API calls anywhere in this package --
see keywords.py's docstring.
"""

from .analyzer import analyze_text, analyze_news_item

__all__ = ["analyze_text", "analyze_news_item"]
