"""
app/engines/news_intelligence/tests/test_adapter.py
Run: python scripts/app/engines/news_intelligence/tests/test_adapter.py
"""

import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
_scripts_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_here))))
sys.path.insert(0, _scripts_dir)

from app.engines.news_intelligence.adapters.decision_adapter import NewsDecisionAdapter
from app.engines.news_intelligence.cache.news_cache import NewsCache
from app.engines.news_intelligence.models.news import NewsCategory, NewsItem
from app.engines.news_intelligence.utils.time_utils import now_ist_iso


def check(condition, message):
    assert condition, f"FAILED: {message}"
    print(f"PASS: {message}")


def _put_item(cache, headline, summary, symbol, category=NewsCategory.EARNINGS):
    item = NewsItem(
        news_id=headline, headline=headline, summary=summary, source="TEST",
        published_at=now_ist_iso(), fetched_at=now_ist_iso(), category=category,
        symbols=[symbol],
    )
    cache.put(item)
    return item


def test_no_news_returns_neutral_zero_score():
    cache = NewsCache(ttl_seconds=60)
    adapter = NewsDecisionAdapter(cache=cache)
    result = adapter.get_news_score("TCS")
    check(result.news_score == 0.0, "symbol with zero cached articles gets a neutral 0.0 score")
    check(result.article_count == 0, "article_count is 0 when nothing is cached")


def test_positive_news_yields_positive_score():
    cache = NewsCache(ttl_seconds=60)
    _put_item(cache, "Reliance reports record profit, beats estimates",
              "Strong growth in refining margins drives profit jump", "RELIANCE")
    adapter = NewsDecisionAdapter(cache=cache)
    result = adapter.get_news_score("RELIANCE")
    check(result.news_score > 0, "positive earnings news yields a positive news_score")
    check(result.sentiment in ("positive", "very_positive"), "sentiment label reflects the positive tone")
    check(result.article_count == 1, "article_count reflects the one cached item")


def test_negative_news_yields_negative_score():
    cache = NewsCache(ttl_seconds=60)
    _put_item(cache, "Company faces SEBI penalty after fraud investigation",
              "Regulator fines the company following a probe into circular trading",
              "TCS", category=NewsCategory.REGULATORY)
    adapter = NewsDecisionAdapter(cache=cache)
    result = adapter.get_news_score("TCS")
    check(result.news_score < 0, "fraud/penalty news yields a negative news_score")
    check(result.sentiment in ("negative", "very_negative"), "sentiment label reflects the negative tone")


def test_result_is_json_serializable_dict():
    cache = NewsCache(ttl_seconds=60)
    _put_item(cache, "Infosys wins large IT services contract", "New deal signed", "INFY")
    adapter = NewsDecisionAdapter(cache=cache)
    result_dict = adapter.get_news_score("INFY").to_dict()
    import json
    serialized = json.dumps(result_dict)  # raises if not JSON-serializable
    check(isinstance(serialized, str), "NewsScoreResult.to_dict() is JSON-serializable")
    for key in ("symbol", "news_score", "sentiment", "impact", "confidence", "reasons", "article_count"):
        check(key in result_dict, f"result dict includes '{key}'")


def test_module_level_get_news_score_wrapper():
    from app.engines.news_intelligence.adapters.decision_adapter import get_news_score
    result = get_news_score("SOME_SYMBOL_WITH_NO_NEWS")
    check(isinstance(result, dict), "module-level get_news_score() returns a plain dict")


if __name__ == "__main__":
    tests = [obj for name, obj in list(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
    print(f"\n{len(tests)} test functions passed.")
