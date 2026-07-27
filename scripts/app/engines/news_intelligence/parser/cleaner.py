"""
app/engines/news_intelligence/parser/cleaner.py -- text-level cleanup only.
No field-mapping/business logic here -- that's normalizer.py's job. This
module answers only "is this string clean," never "which NewsItem field
does this belong in."
"""

from __future__ import annotations

from ..utils.text_utils import normalize_text, truncate

MAX_HEADLINE_LEN = 300
MAX_SUMMARY_LEN = 2000


def clean_headline(raw: str) -> str:
    return truncate(normalize_text(raw), MAX_HEADLINE_LEN)


def clean_summary(raw: str) -> str:
    return truncate(normalize_text(raw), MAX_SUMMARY_LEN)


def is_malformed(headline: str, summary: str) -> bool:
    """A minimal sanity check malformed feed/announcement rows fail --
    used by parser.py to silently drop unusable rows rather than emitting
    a garbage NewsItem downstream engines would have to defend against."""
    if not headline or len(headline.strip()) < 5:
        return True
    if headline.strip().lower() in {"untitled", "n/a", "null", "none"}:
        return True
    return False
