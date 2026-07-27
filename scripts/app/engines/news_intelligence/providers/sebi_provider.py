"""
app/engines/news_intelligence/providers/sebi_provider.py -- SEBI press
releases / circulars (free, public listing page, no API key).
------------------------------------------------------------------------------
Same scraping approach as rbi_provider.py (see that module's docstring for
the rationale) -- SEBI's public site also has no press-release JSON API.
"""

from __future__ import annotations

from logging_setup import get_logger
from ..utils.http_client import fetch_text
from ..utils.time_utils import now_ist_iso
from .base_provider import BaseProvider

_logger = get_logger("news_intelligence")

_PRESS_RELEASES_URL = "https://www.sebi.gov.in/media/press-releases.html"
_BASE_URL = "https://www.sebi.gov.in"


class SEBIProvider(BaseProvider):
    name = "SEBI"

    def fetch_raw(self) -> list[dict]:
        html_text = fetch_text(_PRESS_RELEASES_URL, timeout=self._timeout(), retries=self._retries())
        if not html_text:
            return []
        try:
            return self._extract_rows(html_text)
        except Exception as exc:  # noqa: BLE001
            _logger.warning(f"NEWS_PROVIDER SEBI parse failed: {exc}")
            return []

    @staticmethod
    def _extract_rows(html_text: str) -> list[dict]:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html_text, "html.parser")
        rows: list[dict] = []

        for anchor in soup.find_all("a", href=True):
            href = anchor["href"]
            title = anchor.get_text(strip=True)
            if not title or len(title) < 8:
                continue
            if "/media/" not in href.lower() and "press" not in href.lower():
                continue
            full_url = href if href.startswith("http") else _BASE_URL + href
            rows.append({
                "title": title,
                "url": full_url,
                "published_raw": None,
                "fetched_at": now_ist_iso(),
                "source_id": full_url,
            })

        return rows
