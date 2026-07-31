"""
app/engines/news_intelligence/providers/sebi_provider.py -- SEBI press
releases / circulars / orders (free, public RSS feed, no API key).
------------------------------------------------------------------------------
FIX (2026-07-31): the previous implementation scraped
https://www.sebi.gov.in/media/press-releases.html, which now 404s --
confirmed live. SEBI's own https://www.sebi.gov.in/rss.html page
documents their official RSS feed instead, which was verified live
the same day (valid RSS 2.0, current items). Switched to parsing that
feed with the same stdlib xml.etree.ElementTree approach
rss_provider.py already uses, instead of a bespoke BeautifulSoup HTML
scrape of a page that can (and did) move out from under us -- an RSS
feed is a much more stable contract for a government site to keep
than a specific HTML page's markup.

Kept as its own provider class (rather than folded into the generic
RSSProvider's settings.yaml feed list) because normalizer.py's
normalize_sebi() is keyed on provider_name == "SEBI" specifically
(distinct default_category=REGULATORY) -- this preserves that
contract unchanged. Output shape (title/url/published_raw/source_id)
is also unchanged, so normalizer.py needed no edits.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from logging_setup import get_logger
from ..utils.http_client import fetch_text
from ..utils.text_utils import normalize_text
from ..utils.time_utils import now_ist_iso
from .base_provider import BaseProvider

_logger = get_logger("news_intelligence")

_RSS_URL = "https://www.sebi.gov.in/sebirss.xml"


class SEBIProvider(BaseProvider):
    name = "SEBI"

    def fetch_raw(self) -> list[dict]:
        xml_text = fetch_text(_RSS_URL, timeout=self._timeout(), retries=self._retries())
        if not xml_text:
            return []
        try:
            return self._parse_feed(xml_text)
        except ET.ParseError as exc:
            _logger.warning(f"NEWS_PROVIDER SEBI malformed feed error={exc}")
            return []
        except Exception as exc:  # noqa: BLE001
            _logger.warning(f"NEWS_PROVIDER SEBI parse failed: {exc}")
            return []

    @staticmethod
    def _parse_feed(xml_text: str) -> list[dict]:
        root = ET.fromstring(xml_text)
        rows: list[dict] = []

        for item in root.findall(".//item"):
            title = (item.findtext("title") or "").strip()
            if not title:
                continue
            link = (item.findtext("link") or "").strip()
            rows.append({
                "title": normalize_text(title),
                "url": link,
                "published_raw": (item.findtext("pubDate") or "").strip() or None,
                "fetched_at": now_ist_iso(),
                "source_id": link or title,
            })

        return rows

