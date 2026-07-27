"""
app/engines/news_intelligence/tests/test_error_handling.py
Run: python scripts/app/engines/news_intelligence/tests/test_error_handling.py

Every provider's fetch_raw() contract is "never raise" (see
providers/base_provider.py's docstring) -- these tests exercise that
contract directly against real provider classes with network mocked out
via monkeypatching utils.http_client, plus a few other error-path checks
(malformed cache access, invalid config path) that don't fit naturally
into the other test files.
"""

import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
_scripts_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_here))))
sys.path.insert(0, _scripts_dir)

from app.engines.news_intelligence.cache.news_cache import NewsCache
from app.engines.news_intelligence.providers.bse_provider import BSEProvider
from app.engines.news_intelligence.providers.nse_provider import NSEProvider
from app.engines.news_intelligence.providers.rss_provider import RSSProvider


def check(condition, message):
    assert condition, f"FAILED: {message}"
    print(f"PASS: {message}")


class _FakeConfig:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


def test_nse_provider_survives_network_exception(monkeypatch=None):
    provider = NSEProvider(_FakeConfig(timeout_seconds=1, retries=0))

    class ExplodingSession:
        def __init__(self):
            self.headers = {}

        def update(self, *a, **k):
            pass

        def get(self, *a, **k):
            raise ConnectionError("simulated DNS failure")

    import app.engines.news_intelligence.providers.nse_provider as nse_mod
    original_requests = sys.modules.get("requests")

    class FakeRequestsModule:
        @staticmethod
        def Session():
            s = ExplodingSession()
            s.headers = type("H", (), {"update": lambda self, *a, **k: None})()
            return s

    sys.modules["requests"] = FakeRequestsModule()
    try:
        result = provider.fetch_raw()
        check(result == [], "NSEProvider.fetch_raw() returns [] (not an exception) on a network failure")
    finally:
        if original_requests is not None:
            sys.modules["requests"] = original_requests


def test_bse_provider_survives_bad_json(monkeypatch=None):
    import app.engines.news_intelligence.providers.bse_provider as bse_mod

    def fake_fetch_json(*a, **k):
        return None  # simulates fetch_json's own decode-failure -> None contract

    original = bse_mod.fetch_json
    bse_mod.fetch_json = fake_fetch_json
    try:
        provider = BSEProvider(_FakeConfig(timeout_seconds=1, retries=0))
        result = provider.fetch_raw()
        check(result == [], "BSEProvider.fetch_raw() returns [] when the JSON fetch fails")
    finally:
        bse_mod.fetch_json = original


def test_rss_provider_survives_malformed_xml(monkeypatch=None):
    import app.engines.news_intelligence.providers.rss_provider as rss_mod

    def fake_fetch_text(*a, **k):
        return "<rss><channel><item><title>Unclosed"  # deliberately malformed XML

    original = rss_mod.fetch_text
    rss_mod.fetch_text = fake_fetch_text
    try:
        feed_cfg = _FakeConfig(name="test", url="http://example.com/feed")
        provider = RSSProvider(_FakeConfig(timeout_seconds=1, retries=0, feeds=[feed_cfg]))
        result = provider.fetch_raw()
        check(result == [], "RSSProvider.fetch_raw() returns [] on malformed XML, does not raise")
    finally:
        rss_mod.fetch_text = original


def test_cache_get_of_missing_key_returns_none_not_raise():
    cache = NewsCache(ttl_seconds=60)
    check(cache.get("does-not-exist") is None, "cache.get() on a missing key returns None, not KeyError")
    check(cache.contains("does-not-exist") is False, "cache.contains() on a missing key returns False")


def test_config_falls_back_gracefully_on_bad_yaml(tmp_path=None):
    import tempfile

    from app.engines.news_intelligence.config.loader import reload_settings

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write("providers:\n  nse:\n    enabled: not_a_boolean_value_###\n")
        bad_path = f.name
    try:
        settings = reload_settings(bad_path)
        check(settings is not None, "invalid YAML/type mismatch does not crash reload_settings()")
    finally:
        os.unlink(bad_path)
        reload_settings()  # restore the real settings.yaml for any subsequent test run


if __name__ == "__main__":
    tests = [obj for name, obj in list(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
    print(f"\n{len(tests)} test functions passed.")
