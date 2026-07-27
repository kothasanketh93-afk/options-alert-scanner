"""
app/engines/news_intelligence/utils/text_utils.py -- shared text primitives.
Stdlib-only (re, html, unicodedata) -- no NLP dependency at this layer.
"""

from __future__ import annotations

import hashlib
import html
import re
import unicodedata

_WHITESPACE_RE = re.compile(r"\s+")
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def strip_html(text: str) -> str:
    if not text:
        return ""
    text = _HTML_TAG_RE.sub(" ", text)
    text = html.unescape(text)
    return text


def collapse_whitespace(text: str) -> str:
    if not text:
        return ""
    return _WHITESPACE_RE.sub(" ", text).strip()


def normalize_text(text: str) -> str:
    """Full pipeline: strip HTML, unescape entities, normalize unicode
    (NFKC -- collapses full-width punctuation some NSE/BSE feeds use),
    collapse whitespace."""
    text = strip_html(text or "")
    text = unicodedata.normalize("NFKC", text)
    return collapse_whitespace(text)


def content_hash(*parts: str) -> str:
    """Stable hash used as NewsItem.news_id -- content-based (not the
    source's own id) so the SAME announcement carried by two different
    providers collapses to one news_id in the dedup cache. See
    collector/deduplicator.py for the near-duplicate (fuzzy) layer this
    exact-hash pairs with."""
    joined = "||".join((p or "").strip().lower() for p in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:24]


def truncate(text: str, max_len: int) -> str:
    text = text or ""
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "\u2026"
