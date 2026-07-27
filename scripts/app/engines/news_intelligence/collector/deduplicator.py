"""
app/engines/news_intelligence/collector/deduplicator.py -- two-layer
duplicate detection.
------------------------------------------------------------------------------
Layer 1 (exact): NewsItem.news_id is already a content hash (see
utils/text_utils.content_hash, used in parser/normalizer.py) -- an exact
duplicate (same source+headline) collapses for free via NewsCache.put()'s
own news_id-keyed dict, no extra work needed here.

Layer 2 (fuzzy, this module): the SAME announcement often gets slightly
different headlines across providers -- an NSE filing title vs. an RSS
aggregator's rewritten headline for the same event. is_near_duplicate()
does a cheap token-set similarity check (difflib) against recently cached
items for the same symbol, within a configurable time window, so these
don't both surface as "new" news.
"""

from __future__ import annotations

import difflib
from datetime import datetime

from ..cache.news_cache import NewsCache
from ..models.news import NewsItem


def _headline_similarity(a: str, b: str) -> float:
    tokens_a = set(a.lower().split())
    tokens_b = set(b.lower().split())
    if not tokens_a or not tokens_b:
        return 0.0
    # Jaccard on tokens is fast and works well for near-duplicate headlines;
    # SequenceMatcher as a tie-breaker for close-but-not-identical token sets.
    jaccard = len(tokens_a & tokens_b) / len(tokens_a | tokens_b)
    seq_ratio = difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()
    return max(jaccard, seq_ratio)


def is_near_duplicate(item: NewsItem, cache: NewsCache, *, threshold: float = 0.88,
                       window_minutes: int = 180) -> bool:
    """True if `item` is a near-duplicate of something already cached for
    ANY symbol it maps to, published within `window_minutes` of it."""
    if not item.symbols:
        return False

    try:
        item_time = datetime.fromisoformat(item.published_at)
    except Exception:
        item_time = None

    for symbol in item.symbols:
        for existing in cache.get_by_symbol(symbol, limit=25):
            if existing.news_id == item.news_id:
                continue
            if item_time is not None:
                try:
                    existing_time = datetime.fromisoformat(existing.published_at)
                    if abs((item_time - existing_time).total_seconds()) > window_minutes * 60:
                        continue
                except Exception:
                    pass
            if _headline_similarity(item.headline, existing.headline) >= threshold:
                return True
    return False
