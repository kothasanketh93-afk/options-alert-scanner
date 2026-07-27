"""
app/engines/news_intelligence/models/news.py -- the normalized news record.
------------------------------------------------------------------------------
Every provider returns raw, source-specific payloads (NSE JSON, BSE JSON,
RBI/SEBI HTML press-release listings, RSS/Atom XML). parser/normalizer.py's
job is to turn ALL of them into this one shape before anything downstream
(mapper, sentiment, impact, cache) ever sees a news item. Nothing outside
parser/ should construct a NewsItem directly from provider-raw data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class NewsCategory(str, Enum):
    """Coarse classification used by impact/confidence.py and the
    Telegram-facing summary the adapter will eventually produce."""
    CORPORATE_ANNOUNCEMENT = "corporate_announcement"
    EARNINGS = "earnings"
    REGULATORY = "regulatory"          # SEBI circulars/orders
    MONETARY_POLICY = "monetary_policy"  # RBI press releases
    GOVERNMENT = "government"          # ministry/government notifications
    MARKET_GENERAL = "market_general"  # generic RSS market news
    CORPORATE_ACTION = "corporate_action"  # dividend/bonus/split/buyback
    MERGER_ACQUISITION = "merger_acquisition"
    LEGAL = "legal"                    # litigation, investigation
    UNKNOWN = "unknown"


@dataclass
class NewsItem:
    """The single normalized shape every downstream engine consumes.

    `news_id` is a stable content hash (see collector/deduplicator.py) --
    NOT the source's own article id, since NSE/BSE/RSS ids are not
    comparable across providers and duplicate stories about the same
    announcement often appear on more than one source with different
    native ids.
    """
    news_id: str
    headline: str
    summary: str
    source: str                 # e.g. "NSE", "BSE", "RBI", "SEBI", "RSS:moneycontrol"
    published_at: str           # ISO 8601, IST-normalized
    fetched_at: str             # ISO 8601, when the collector saw it
    category: NewsCategory
    companies: list[str] = field(default_factory=list)      # detected company names
    symbols: list[str] = field(default_factory=list)        # detected NSE symbols
    sectors: list[str] = field(default_factory=list)        # detected sectors
    tags: list[str] = field(default_factory=list)
    url: Optional[str] = None
    description: Optional[str] = None
    raw_source_id: Optional[str] = None   # provider's own id, kept for traceability
    language: str = "en"

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        d["category"] = self.category.value if isinstance(self.category, NewsCategory) else self.category
        return d

    @staticmethod
    def from_dict(d: dict) -> "NewsItem":
        d = dict(d)
        cat = d.get("category", NewsCategory.UNKNOWN)
        d["category"] = cat if isinstance(cat, NewsCategory) else NewsCategory(cat)
        return NewsItem(**d)
