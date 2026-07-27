"""
app/engines/news_intelligence/tests/test_config.py
Run: python scripts/app/engines/news_intelligence/tests/test_config.py
"""

import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
_scripts_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_here))))
sys.path.insert(0, _scripts_dir)

from app.engines.news_intelligence.config import get_settings, reload_settings


def check(condition, message):
    assert condition, f"FAILED: {message}"
    print(f"PASS: {message}")


def test_settings_load_and_singleton():
    s1 = get_settings()
    s2 = get_settings()
    check(s1 is s2, "get_settings() returns a cached singleton")


def test_settings_have_expected_defaults():
    settings = get_settings()
    check(settings.cache.ttl_seconds == 21600, "cache TTL default is 6 hours")
    check(settings.dedup.fuzzy_similarity_threshold == 0.88, "fuzzy dedup threshold default")
    check(settings.impact.very_high_score == 80, "impact very_high threshold default")
    check(settings.providers.nse.enabled is True, "NSE provider enabled by default")
    check(len(settings.providers.rss.feeds) >= 3, "at least 3 default RSS feeds configured")


def test_reload_settings_forces_fresh_read():
    s1 = get_settings()
    s2 = reload_settings()
    check(s2 is not s1, "reload_settings() returns a fresh object, not the cached one")
    check(s2.cache.ttl_seconds == s1.cache.ttl_seconds, "reloaded settings still match file contents")


if __name__ == "__main__":
    tests = [obj for name, obj in list(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
    print(f"\n{len(tests)} test functions passed.")
