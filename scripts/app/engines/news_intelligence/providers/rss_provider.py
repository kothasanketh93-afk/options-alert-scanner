"""
app/engines/news_intelligence/providers/rss_provider.py -- free RSS/Atom
feeds (market news + government notifications, e.g. PIB).
------------------------------------------------------------------------------
Parses with stdlib xml.etree.ElementTree rather than adding a `feedparser`
dependency -- RSS 2.0 and Atom are both simple enough XML shapes that a
~40-line hand-rolled parser covers the feeds this module ships with
(see config/settings.yaml's `providers.rss.feeds` list), without a new
third-party import. Malformed/unparseable feed XML is caught and skipped
per-feed (one bad feed must not block the others -- same "never raise"
contract as every other provider).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from logging_setup import get_logger
from ..utils.http_client import fetch_text
from ..utils.text_utils import normalize_text
from .base_provider import BaseProvider

_logger = get_logger("news_intelligence")

_ATOM_NS = "{http://www.w3.org/2005/Atom}"


class RSSProvider(BaseProvider):
    """Multi-feed provider: `name` stays "RSS" for the class, but each
    returned row carries its own `feed_name` so parser/normalizer.py can
    set NewsItem.source to "RSS:<feed_name>" (distinct sources per feed,
    useful for per-source cache/dedup stats without a separate provider
    class per feed)."""

    name = "RSS"

    def fetch_raw(self) -> list[dict]:
        feeds = list(getattr(self.config, "feeds", []) or [])
        rows: list[dict] = []
        for feed in feeds:
            feed_name = getattr(feed, "name", None) or (feed.get("name") if isinstance(feed, dict) else "unknown")
            feed_url = getattr(feed, "url", None) or (feed.get("url") if isinstance(feed, dict) else None)
            if not feed_url:
                continue
            rows.extend(self._fetch_one_feed(feed_name, feed_url))
        return rows

    def _fetch_one_feed(self, feed_name: str, feed_url: str) -> list[dict]:
        xml_text = fetch_text(feed_url, timeout=self._timeout(6.0), retries=self._retries(1))
        if not xml_text:
            return []
        try:
            return self._parse_feed(feed_name, feed_url, xml_text)
        except ET.ParseError as exc:
            _logger.warning(f"NEWS_PROVIDER RSS malformed feed={feed_name} error={exc}")
            return []
        except Exception as exc:  # noqa: BLE001
            _logger.warning(f"NEWS_PROVIDER RSS unexpected error feed={feed_name} error={exc}")
            return []

    @staticmethod
    def _parse_feed(feed_name: str, feed_url: str, xml_text: str) -> list[dict]:
        root = ET.fromstring(xml_text)
        rows: list[dict] = []

        # RSS 2.0: <rss><channel><item>...
        for item in root.findall(".//item"):
            title = (item.findtext("title") or "").strip()
            if not title:
                continue
            rows.append({
                "feed_name": feed_name,
                "title": normalize_text(title),
                "description": normalize_text(item.findtext("description") or ""),
                "link": (item.findtext("link") or "").strip(),
                "pub_date": (item.findtext("pubDate") or "").strip(),
                "guid": (item.findtext("guid") or item.findtext("link") or "").strip(),
                "category": (item.findtext("category") or "").strip(),
            })

        # Atom: <feed><entry>...
        for entry in root.findall(f".//{_ATOM_NS}entry"):
            title = (entry.findtext(f"{_ATOM_NS}title") or "").strip()
            if not title:
                continue
            link_el = entry.find(f"{_ATOM_NS}link")
            link = link_el.get("href") if link_el is not None else ""
            rows.append({
                "feed_name": feed_name,
                "title": normalize_text(title),
                "description": normalize_text(
                    entry.findtext(f"{_ATOM_NS}summary") or entry.findtext(f"{_ATOM_NS}content") or ""
                ),
                "link": (link or "").strip(),
                "pub_date": (entry.findtext(f"{_ATOM_NS}updated") or entry.findtext(f"{_ATOM_NS}published") or "").strip(),
                "guid": (entry.findtext(f"{_ATOM_NS}id") or link or "").strip(),
                "category": "",
            })

        return rows
