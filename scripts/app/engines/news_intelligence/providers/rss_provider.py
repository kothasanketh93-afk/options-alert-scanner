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
            # BUGFIX (2026-08-23, live-log finding): Moneycontrol's feed
            # failed 12 times on one trading day with previews showing
            # an HTML "Login Consent - Moneycontrol" page, not RSS XML
            # at all -- this is an access block (cookie/consent wall or
            # bot detection redirecting the request), not a malformed
            # feed from the source. Bypassing that redirect is out of
            # scope here (would need investigating what
            # headers/cookies/session Moneycontrol's real RSS access
            # requires, unverified) -- this only makes the DIAGNOSIS
            # correct: an HTML-shaped response gets a distinct log tag
            # so a future read of this warning immediately says "access
            # blocked", not "malformed feed", without conflating it with
            # PIB's genuinely-different BOM issue (fixed separately,
            # same date) under one generic error message.
            if "<html" in xml_text[:500].lower() or "<!doctype html" in xml_text[:500].lower():
                _logger.warning(
                    f"NEWS_PROVIDER RSS access_blocked feed={feed_name} -- received an HTML page "
                    f"instead of RSS XML (likely a login/consent wall or bot-detection redirect, "
                    f"not a malformed feed) preview={xml_text[:200]!r}"
                )
                return []
            # FIX (2026-07-31): same diagnostic technique that revealed
            # BSE's real issue (turned out to be a plain "No Record
            # Found!" string, not a broken endpoint) -- a truncated
            # content preview here so the next occurrence of this
            # warning says what's actually being returned instead of
            # just that parsing failed. Needed because pib_press_releases'
            # URL fix (stale Regid corrected) did NOT resolve this live in
            # production despite testing well-formed independently --
            # this preview is how we find out why.
            preview = xml_text[:200]
            _logger.warning(
                f"NEWS_PROVIDER RSS malformed feed={feed_name} error={exc} preview={preview!r}"
            )
            return []
        except Exception as exc:  # noqa: BLE001
            _logger.warning(f"NEWS_PROVIDER RSS unexpected error feed={feed_name} error={exc}")
            return []

    @staticmethod
    def _parse_feed(feed_name: str, feed_url: str, xml_text: str) -> list[dict]:
        # BUGFIX (2026-08-23, live-log finding): PIB's feed consistently
        # failed with "not well-formed (invalid token): line 1, column 1"
        # every single scan cycle (59/59 on a full trading day) -- the
        # preview showed a literal 'ï»¿<?xml...' prefix. That's a UTF-8
        # byte-order-mark (EF BB BF) mis-decoded as three separate
        # Latin-1 characters -- fetch_text() (http_client.py) uses
        # requests' own `.text`, which falls back to a guessed encoding
        # (commonly Latin-1, HTTP's own default for text/* with no
        # explicit charset) when the server doesn't declare one, and
        # that guess doesn't strip/interpret a leading BOM the way
        # `utf-8-sig` would. Stripping BOTH the correctly-decoded BOM
        # character (\ufeff) and this specific mis-decoded triplet from
        # the start of the text before parsing handles it regardless of
        # which decoding path actually produced it, without changing
        # fetch_text()'s general-purpose behavior for every other
        # provider that already works fine.
        xml_text = xml_text.lstrip("\ufeff")
        if xml_text.startswith("ï»¿"):
            xml_text = xml_text[3:]
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
