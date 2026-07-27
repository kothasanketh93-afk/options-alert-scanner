"""
app/engines/news_intelligence/parser/normalizer.py -- provider-raw dict ->
NewsItem.
------------------------------------------------------------------------------
One `_normalize_<source>()` function per provider shape, all converging on
build_news_item() which does the shared cleanup/hashing/category-guess
work. Adding a new provider means adding one new `_normalize_x()` function
here plus a branch in normalize() -- everything else in the package (cache,
mapper, sentiment, impact, adapter) only ever sees the resulting NewsItem
and never needs to know a new source was added.
"""

from __future__ import annotations

from typing import Optional

from ..models.news import NewsItem, NewsCategory
from ..utils.text_utils import content_hash
from ..utils.time_utils import now_ist_iso, parse_to_ist_iso
from . import cleaner

_CATEGORY_KEYWORDS: dict[NewsCategory, tuple[str, ...]] = {
    NewsCategory.EARNINGS: ("quarterly results", "q1 results", "q2 results", "q3 results",
                             "q4 results", "financial results", "net profit", "net loss",
                             "revenue from operations", "board meeting intimation"),
    NewsCategory.CORPORATE_ACTION: ("dividend", "bonus issue", "stock split", "buyback",
                                     "rights issue", "record date"),
    NewsCategory.MERGER_ACQUISITION: ("merger", "acquisition", "acquire", "amalgamation",
                                        "demerger", "stake sale", "slump sale"),
    NewsCategory.LEGAL: ("litigation", "investigation", "penalty", "show cause notice",
                          "court order", "tribunal"),
    NewsCategory.REGULATORY: ("circular", "regulation", "sebi", "compliance",
                               "guidelines"),
    NewsCategory.MONETARY_POLICY: ("repo rate", "monetary policy", "rbi", "mpc",
                                     "inflation", "cash reserve ratio"),
}


def _guess_category(default: NewsCategory, headline: str, summary: str) -> NewsCategory:
    text = f"{headline} {summary}".lower()
    for category, keywords in _CATEGORY_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            return category
    return default


def build_news_item(*, headline: str, summary: str, source: str, published_raw: Optional[str],
                     url: Optional[str], raw_source_id: Optional[str],
                     default_category: NewsCategory, extra_tags: Optional[list[str]] = None) -> Optional[NewsItem]:
    headline = cleaner.clean_headline(headline)
    summary = cleaner.clean_summary(summary)
    if cleaner.is_malformed(headline, summary):
        return None

    published_at = parse_to_ist_iso(published_raw) if published_raw else None
    fetched_at = now_ist_iso()

    return NewsItem(
        news_id=content_hash(source, headline),
        headline=headline,
        summary=summary or headline,
        source=source,
        published_at=published_at or fetched_at,
        fetched_at=fetched_at,
        category=_guess_category(default_category, headline, summary),
        tags=list(extra_tags or []),
        url=url,
        description=summary or None,
        raw_source_id=raw_source_id,
    )


def normalize_nse(raw: dict) -> Optional[NewsItem]:
    headline = raw.get("subject") or raw.get("desc") or raw.get("attchmntText") or ""
    summary = raw.get("attchmntText") or raw.get("desc") or headline
    return build_news_item(
        headline=headline,
        summary=summary,
        source="NSE",
        published_raw=raw.get("an_dt") or raw.get("sort_date") or raw.get("dt"),
        url=raw.get("attchmntFile") or raw.get("pdfFile"),
        raw_source_id=str(raw.get("seq_id") or raw.get("attchmntFile") or ""),
        default_category=NewsCategory.CORPORATE_ANNOUNCEMENT,
        extra_tags=[t for t in [raw.get("symbol")] if t],
    )


def normalize_bse(raw: dict) -> Optional[NewsItem]:
    headline = raw.get("NEWSSUB") or raw.get("HEADLINE") or ""
    summary = raw.get("MORE") or raw.get("NEWSSUB") or headline
    return build_news_item(
        headline=headline,
        summary=summary,
        source="BSE",
        published_raw=raw.get("NEWS_DT") or raw.get("DissemDT"),
        url=raw.get("ATTACHMENTNAME"),
        raw_source_id=str(raw.get("NEWSID") or ""),
        default_category=NewsCategory.CORPORATE_ANNOUNCEMENT,
        extra_tags=[t for t in [raw.get("SCRIP_CD")] if t],
    )


def normalize_rbi(raw: dict) -> Optional[NewsItem]:
    return build_news_item(
        headline=raw.get("title", ""),
        summary=raw.get("title", ""),
        source="RBI",
        published_raw=raw.get("published_raw"),
        url=raw.get("url"),
        raw_source_id=raw.get("source_id"),
        default_category=NewsCategory.MONETARY_POLICY,
    )


def normalize_sebi(raw: dict) -> Optional[NewsItem]:
    return build_news_item(
        headline=raw.get("title", ""),
        summary=raw.get("title", ""),
        source="SEBI",
        published_raw=raw.get("published_raw"),
        url=raw.get("url"),
        raw_source_id=raw.get("source_id"),
        default_category=NewsCategory.REGULATORY,
    )


def normalize_rss(raw: dict) -> Optional[NewsItem]:
    feed_name = raw.get("feed_name", "unknown")
    is_gov_feed = "pib" in feed_name.lower()
    return build_news_item(
        headline=raw.get("title", ""),
        summary=raw.get("description", ""),
        source=f"RSS:{feed_name}",
        published_raw=raw.get("pub_date"),
        url=raw.get("link"),
        raw_source_id=raw.get("guid"),
        default_category=NewsCategory.GOVERNMENT if is_gov_feed else NewsCategory.MARKET_GENERAL,
        extra_tags=[t for t in [raw.get("category")] if t],
    )


_DISPATCH = {
    "NSE": normalize_nse,
    "BSE": normalize_bse,
    "RBI": normalize_rbi,
    "SEBI": normalize_sebi,
    "RSS": normalize_rss,
}


def normalize(provider_name: str, raw: dict) -> Optional[NewsItem]:
    fn = _DISPATCH.get(provider_name)
    if fn is None:
        return None
    return fn(raw)
