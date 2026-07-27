"""
app/engines/news_intelligence/providers/rbi_provider.py -- RBI press
releases (free, public listing page, no API key).
------------------------------------------------------------------------------
RBI does not publish a JSON API for press releases, so this scrapes the
public press-releases listing page with BeautifulSoup (a free, standard
HTML-parsing library -- see requirements-news_intelligence.txt). Uses a
conservative CSS-selector-with-fallback approach: RBI's page markup has
changed shape before, so `_extract_rows()` tries a couple of known
selectors and returns an empty list (never raises) if none match, rather
than crashing the whole collector cycle over one source's markup drift.
"""

from __future__ import annotations

from logging_setup import get_logger
from ..utils.http_client import fetch_text
from ..utils.time_utils import now_ist_iso
from .base_provider import BaseProvider

_logger = get_logger("news_intelligence")

_PRESS_RELEASES_URL = "https://website.rbi.org.in/web/rbi/press-releases"
_BASE_URL = "https://website.rbi.org.in"


class RBIProvider(BaseProvider):
    name = "RBI"

    def fetch_raw(self) -> list[dict]:
        html_text = fetch_text(_PRESS_RELEASES_URL, timeout=self._timeout(), retries=self._retries())
        if not html_text:
            return []
        try:
            return self._extract_rows(html_text)
        except Exception as exc:  # noqa: BLE001
            _logger.warning(f"NEWS_PROVIDER RBI parse failed: {exc}")
            return []

    @staticmethod
    def _extract_rows(html_text: str) -> list[dict]:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html_text, "html.parser")
        rows: list[dict] = []

        # RBI's press-release listing renders as anchor tags whose text is
        # the release title, grouped under date headers. We collect every
        # anchor that looks like a press-release link ("/press-releases" or
        # PDF/press-release URL patterns) rather than depending on a single
        # brittle CSS class name.
        for anchor in soup.find_all("a", href=True):
            href = anchor["href"]
            title = anchor.get_text(strip=True)
            if not title or len(title) < 8:
                continue
            if "press-release" not in href.lower() and "pressrelease" not in href.lower():
                continue
            full_url = href if href.startswith("http") else _BASE_URL + href
            rows.append({
                "title": title,
                "url": full_url,
                "published_raw": None,  # RBI's list page doesn't reliably expose a
                                          # per-row machine-readable date; normalizer
                                          # falls back to fetched_at (documented there).
                "fetched_at": now_ist_iso(),
                "source_id": full_url,
            })

        return rows
