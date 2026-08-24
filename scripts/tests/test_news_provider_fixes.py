"""
tests/test_news_provider_fixes.py
------------------------------------------------------------------------------
Plain assert-based tests, same convention as this repo's other tests/*.py
files (no pytest dependency).

Covers three 2026-08-23 fixes found from a real, full-trading-day log
(59 scan cycles, 21-Aug), each reproduced with the EXACT preview text
observed live:

  A. PIB RSS: UTF-8 BOM mis-decoded as 'ï»¿' prefix (59/59 cycles) --
     now stripped before parsing.
  B. BSE: literal "No Record Found!" string (59/59 cycles) -- now
     recognized as a normal empty result, not a warning-worthy anomaly.
  C. Moneycontrol RSS: HTML login/consent page instead of RSS XML
     (12/59 cycles) -- now tagged as access_blocked, distinct from a
     genuinely malformed feed, rather than conflated with PIB's issue.

These are informational/context-only providers (news_intelligence never
feeds probability/scoring/alert eligibility) -- these fixes are pure
data-quality/logging-noise corrections, not trading-logic changes.
"""

import os
import sys
from unittest import mock

_here = os.path.dirname(os.path.abspath(__file__))
_scripts_dir = os.path.dirname(_here)
sys.path.insert(0, _scripts_dir)

from app.engines.news_intelligence.providers.rss_provider import RSSProvider
from app.engines.news_intelligence.providers.bse_provider import BSEProvider


def _make_rss_provider(feeds):
    provider = RSSProvider.__new__(RSSProvider)
    provider.config = mock.Mock(feeds=feeds)
    provider._timeout = lambda *a: 6.0
    provider._retries = lambda *a: 1
    return provider


def _make_bse_provider():
    provider = BSEProvider.__new__(BSEProvider)
    provider._timeout = lambda *a: 6.0
    provider._retries = lambda *a: 1
    return provider


# ---------------------------------------------------------------------------
# A. PIB BOM fix
# ---------------------------------------------------------------------------

def test_a_pib_bom_prefix_no_longer_fails_to_parse():
    # Exact preview shape observed live: 'ï»¿<?xml version="1.0" ...'
    pib_xml = (
        'ï»¿<?xml version="1.0" encoding="utf-8"?>'
        '<rss version="2.0"><channel><title>Press Information Bureau</title>'
        '<item><title>Test News Item</title></item></channel></rss>'
    )
    with mock.patch("app.engines.news_intelligence.providers.rss_provider.fetch_text", return_value=pib_xml):
        provider = _make_rss_provider([{"name": "pib_press_releases", "url": "http://x"}])
        result = provider.fetch_raw()
    assert len(result) == 1, f"A: BOM-prefixed feed must still parse successfully, got {result}"
    assert result[0]["title"] == "Test News Item"
    print("test_a_pib_bom_prefix_no_longer_fails_to_parse passed")


def test_a_variant_real_bom_character_also_stripped():
    """Companion check: a correctly-decoded \\ufeff BOM character (not
    just the mis-decoded 'ï»¿' triplet) must also be handled."""
    xml_with_real_bom = (
        "\ufeff<?xml version=\"1.0\"?><rss><channel><item><title>X</title></item></channel></rss>"
    )
    with mock.patch("app.engines.news_intelligence.providers.rss_provider.fetch_text", return_value=xml_with_real_bom):
        provider = _make_rss_provider([{"name": "some_feed", "url": "http://x"}])
        result = provider.fetch_raw()
    assert len(result) == 1
    print("test_a_variant_real_bom_character_also_stripped passed")


# ---------------------------------------------------------------------------
# B. BSE "No Record Found!" fix
# ---------------------------------------------------------------------------

def test_b_no_record_found_is_silent_empty_not_a_warning():
    with mock.patch("app.engines.news_intelligence.providers.bse_provider.fetch_json", return_value="No Record Found!"):
        provider = _make_bse_provider()
        result = provider.fetch_raw()
    assert result == [], f"B: 'No Record Found!' must degrade to an empty list, got {result}"
    print("test_b_no_record_found_is_silent_empty_not_a_warning passed")


def test_b_variant_case_and_whitespace_insensitive():
    with mock.patch("app.engines.news_intelligence.providers.bse_provider.fetch_json", return_value="  no record found!  "):
        provider = _make_bse_provider()
        result = provider.fetch_raw()
    assert result == []
    print("test_b_variant_case_and_whitespace_insensitive passed")


def test_b_variant_genuinely_unexpected_string_still_warns():
    """A DIFFERENT, non-benign string must still hit the warning path
    -- this fix must not silence genuinely unexpected payloads."""
    with mock.patch("app.engines.news_intelligence.providers.bse_provider.fetch_json", return_value="Internal Server Error"):
        provider = _make_bse_provider()
        with mock.patch("app.engines.news_intelligence.providers.bse_provider._logger") as mock_logger:
            result = provider.fetch_raw()
            assert mock_logger.warning.called, "B: a genuinely unexpected string must still warn"
    assert result == []
    print("test_b_variant_genuinely_unexpected_string_still_warns passed")


# ---------------------------------------------------------------------------
# C. Moneycontrol access-block distinct tagging
# ---------------------------------------------------------------------------

def test_c_html_login_page_tagged_as_access_blocked_not_malformed():
    # Exact preview shape observed live: an HTML consent/login page.
    html_page = (
        "<!DOCTYPE html>\n<html>\n  <head>\n    <meta charset=\"utf-8\">\n"
        "    <title>Login Consent - Moneycontrol</title>\n  </head>\n</html>"
    )
    with mock.patch("app.engines.news_intelligence.providers.rss_provider.fetch_text", return_value=html_page):
        provider = _make_rss_provider([{"name": "moneycontrol_business", "url": "http://x"}])
        with mock.patch("app.engines.news_intelligence.providers.rss_provider._logger") as mock_logger:
            result = provider.fetch_raw()
            assert mock_logger.warning.called
            warning_text = mock_logger.warning.call_args[0][0]
    assert "access_blocked" in warning_text, f"C: must be tagged access_blocked, got: {warning_text}"
    assert result == []
    print("test_c_html_login_page_tagged_as_access_blocked_not_malformed passed")


def test_c_variant_genuine_xml_malformation_still_generic_message():
    """A REAL XML malformation (not HTML) must still get the original
    generic 'malformed feed' message, not be misdiagnosed as a block."""
    genuinely_broken_xml = "<rss><channel><item><title>Unclosed"
    with mock.patch("app.engines.news_intelligence.providers.rss_provider.fetch_text", return_value=genuinely_broken_xml):
        provider = _make_rss_provider([{"name": "some_other_feed", "url": "http://x"}])
        with mock.patch("app.engines.news_intelligence.providers.rss_provider._logger") as mock_logger:
            result = provider.fetch_raw()
            assert mock_logger.warning.called
            warning_text = mock_logger.warning.call_args[0][0]
    assert "malformed feed" in warning_text
    assert "access_blocked" not in warning_text
    assert result == []
    print("test_c_variant_genuine_xml_malformation_still_generic_message passed")


if __name__ == "__main__":
    test_a_pib_bom_prefix_no_longer_fails_to_parse()
    test_a_variant_real_bom_character_also_stripped()
    test_b_no_record_found_is_silent_empty_not_a_warning()
    test_b_variant_case_and_whitespace_insensitive()
    test_b_variant_genuinely_unexpected_string_still_warns()
    test_c_html_login_page_tagged_as_access_blocked_not_malformed()
    test_c_variant_genuine_xml_malformation_still_generic_message()
    print("\nAll news-provider-fix tests (A-C) passed.")
