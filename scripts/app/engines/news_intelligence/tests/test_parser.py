"""
app/engines/news_intelligence/tests/test_parser.py
Run: python scripts/app/engines/news_intelligence/tests/test_parser.py
"""

import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
_scripts_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_here))))
sys.path.insert(0, _scripts_dir)

from app.engines.news_intelligence.models.news import NewsCategory
from app.engines.news_intelligence.parser import cleaner, parser
from app.engines.news_intelligence.parser.normalizer import normalize


def check(condition, message):
    assert condition, f"FAILED: {message}"
    print(f"PASS: {message}")


def test_clean_headline_strips_html_and_truncates():
    dirty = "<b>Reliance</b>&nbsp;announces&nbsp;results   " + ("x" * 400)
    cleaned = cleaner.clean_headline(dirty)
    check("<b>" not in cleaned, "HTML tags stripped from headline")
    check("&nbsp;" not in cleaned, "HTML entities unescaped")
    check(len(cleaned) <= cleaner.MAX_HEADLINE_LEN, "headline truncated to MAX_HEADLINE_LEN")


def test_is_malformed_catches_junk_rows():
    check(cleaner.is_malformed("", "summary") is True, "empty headline is malformed")
    check(cleaner.is_malformed("N/A", "x") is True, "'N/A' headline is malformed")
    check(cleaner.is_malformed("Reliance posts strong results", "x") is False,
          "a real headline is not malformed")


def test_normalize_nse_maps_fields():
    raw = {
        "subject": "Reliance Industries: Board Meeting Intimation",
        "attchmntText": "Board will meet on 27-Jul-2026 to consider Q1 results.",
        "an_dt": "27-Jul-2026 09:15:00",
        "attchmntFile": "http://nsearchives.example/announcement.pdf",
        "seq_id": "12345",
        "symbol": "RELIANCE",
    }
    item = normalize("NSE", raw)
    check(item is not None, "NSE raw row normalizes to a NewsItem")
    check(item.source == "NSE", "source is set to NSE")
    check(item.category == NewsCategory.EARNINGS, "board meeting intimation maps to EARNINGS category")
    check(item.url == raw["attchmntFile"], "attachment URL carried through")
    check(item.raw_source_id == "12345", "raw_source_id preserved for traceability")


def test_normalize_rss_distinguishes_government_feeds():
    raw_pib = {
        "feed_name": "pib_press_releases", "title": "Government announces new policy",
        "description": "Details of the new policy.", "link": "http://pib.example/1",
        "pub_date": "Mon, 27 Jul 2026 09:00:00 +0530", "guid": "g1", "category": "",
    }
    raw_market = {
        "feed_name": "moneycontrol_business", "title": "Sensex rallies 500 points",
        "description": "Markets close higher.", "link": "http://mc.example/1",
        "pub_date": "Mon, 27 Jul 2026 15:30:00 +0530", "guid": "g2", "category": "",
    }
    item_pib = normalize("RSS", raw_pib)
    item_market = normalize("RSS", raw_market)
    check(item_pib.category == NewsCategory.GOVERNMENT, "PIB feed rows classified as GOVERNMENT")
    check(item_market.category == NewsCategory.MARKET_GENERAL, "non-gov RSS rows classified as MARKET_GENERAL")
    check(item_pib.source == "RSS:pib_press_releases", "source carries the specific feed name")


def test_parse_batch_skips_malformed_rows_without_raising():
    raw_rows = [
        {"subject": "", "an_dt": None},           # malformed -> dropped
        {"subject": "Valid headline here", "an_dt": "27-Jul-2026", "symbol": "TCS"},
    ]
    parsed = parser.parse_batch("NSE", raw_rows)
    check(len(parsed) == 1, "malformed row silently dropped, valid row kept")


def test_parse_one_unknown_provider_returns_none():
    check(parser.parse_one("UNKNOWN_SOURCE", {"x": 1}) is None,
          "unrecognized provider name returns None, not an exception")


if __name__ == "__main__":
    tests = [obj for name, obj in list(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
    print(f"\n{len(tests)} test functions passed.")
