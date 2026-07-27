"""
app/engines/news_intelligence/tests/test_mapper.py
Run: python scripts/app/engines/news_intelligence/tests/test_mapper.py
"""

import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
_scripts_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_here))))
sys.path.insert(0, _scripts_dir)

from app.engines.news_intelligence.mapper import sector_mapper
from app.engines.news_intelligence.mapper.company_mapper import map_news_item
from app.engines.news_intelligence.mapper.symbol_mapper import find_symbols_in_text
from app.engines.news_intelligence.models.news import NewsCategory, NewsItem
from app.engines.news_intelligence.utils.time_utils import now_ist_iso


def check(condition, message):
    assert condition, f"FAILED: {message}"
    print(f"PASS: {message}")


def test_exact_symbol_match():
    matches = find_symbols_in_text("Reliance Industries reports record profit")
    symbols = [m.symbol for m in matches]
    check("RELIANCE" in symbols, "exact company-name match detects RELIANCE")
    check(matches[symbols.index("RELIANCE")].confidence == 1.0, "exact match has confidence 1.0")


def test_short_symbol_does_not_false_positive_inside_words():
    # "LT" (Larsen & Toubro) must not match inside unrelated words like
    # "multiple" -- regression test for the word-boundary fix.
    matches = find_symbols_in_text("Regulator fines multiple entities for non-compliance")
    symbols = [m.symbol for m in matches]
    check("LT" not in symbols, "'LT' does not false-positive-match inside 'multiple'")


def test_ambiguous_conglomerate_brand_not_matched_from_single_token():
    # "Tata" alone (e.g. inside a generic PIB/government notification with
    # no Tata company actually mentioned) should not fan out to every Tata
    # group company via the fuzzy pass.
    matches = find_symbols_in_text("SEBI penalizes brokers for fraud in circular trading probe")
    check(len(matches) == 0, "unrelated regulatory text produces no spurious company matches")


def test_no_match_on_generic_text():
    matches = find_symbols_in_text("The weather today is pleasant with light rain expected")
    check(matches == [], "generic non-financial text produces zero matches")


def test_sector_lookup():
    check(sector_mapper.sector_for_symbol("TCS") == "IT", "TCS maps to IT sector")
    check(sector_mapper.sector_for_symbol("HDFCBANK") == "Banking", "HDFCBANK maps to Banking sector")
    check(sector_mapper.sector_for_symbol("NOTASYMBOL") == "Unclassified", "unknown symbol maps to Unclassified")


def test_map_news_item_end_to_end():
    item = NewsItem(
        news_id="test1", headline="Infosys wins large multi-year IT services deal",
        summary="Infosys signs a major outsourcing contract with a European client.",
        source="RSS:test", published_at=now_ist_iso(), fetched_at=now_ist_iso(),
        category=NewsCategory.CORPORATE_ANNOUNCEMENT,
    )
    result = map_news_item(item)
    check("INFY" in result.symbols, "map_news_item detects INFY from headline text")
    check("IT" in result.sectors, "map_news_item resolves the IT sector for INFY")
    check(result.confidence > 0, "map_news_item reports a positive confidence when a match is found")


def test_map_news_item_no_match_returns_empty_result():
    item = NewsItem(
        news_id="test2", headline="Local municipal body announces road repair schedule",
        summary="Roadworks to begin next week in several wards.",
        source="RSS:test", published_at=now_ist_iso(), fetched_at=now_ist_iso(),
        category=NewsCategory.GOVERNMENT,
    )
    result = map_news_item(item)
    check(result.symbols == [], "no-match news item returns an empty symbols list")
    check(result.confidence == 0.0, "no-match news item has zero confidence")


if __name__ == "__main__":
    tests = [obj for name, obj in list(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
    print(f"\n{len(tests)} test functions passed.")
