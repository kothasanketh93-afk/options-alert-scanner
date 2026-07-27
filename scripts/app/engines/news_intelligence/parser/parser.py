"""
app/engines/news_intelligence/parser/parser.py -- the one entry point
collector.py calls: raw provider output -> list[NewsItem].
"""

from __future__ import annotations

from typing import Optional

from logging_setup import get_logger
from ..models.news import NewsItem
from . import normalizer

_logger = get_logger("news_intelligence")


def parse_one(provider_name: str, raw: dict) -> Optional[NewsItem]:
    try:
        return normalizer.normalize(provider_name, raw)
    except Exception as exc:  # noqa: BLE001 -- one bad row must not break the batch
        _logger.warning(f"NEWS_PARSER failed provider={provider_name} error={exc}")
        return None


def parse_batch(provider_name: str, raw_items: list[dict]) -> list[NewsItem]:
    parsed: list[NewsItem] = []
    for raw in raw_items:
        item = parse_one(provider_name, raw)
        if item is not None:
            parsed.append(item)
    _logger.info(f"NEWS_PARSER provider={provider_name} raw={len(raw_items)} parsed={len(parsed)}")
    return parsed
