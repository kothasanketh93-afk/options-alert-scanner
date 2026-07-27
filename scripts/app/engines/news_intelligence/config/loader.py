"""
app/engines/news_intelligence/config/loader.py -- loads and validates
settings.yaml.
------------------------------------------------------------------------------
Uses PyYAML to parse the file (a new, but free/lightweight, dependency --
see requirements-news_intelligence.txt) and pydantic to validate/coerce it
into a typed settings object with real defaults, per the "Pydantic" item
in this module's production requirements. This is the ONE place in the
package pydantic is used -- an edge (startup-time config validation), not
the per-news-item hot path, so its cost is paid once per process, not once
per article. If pydantic is not installed, get_settings() falls back to a
plain dict-backed accessor with the identical attribute surface, so the
package still degrades gracefully rather than hard-failing at import time.

get_settings() caches the parsed result per-process (module-level
singleton) -- reload_settings() forces a re-read, used by tests and by any
caller that hot-edits settings.yaml at runtime.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from logging_setup import get_logger

_logger = get_logger("news_intelligence")

_SETTINGS_PATH = os.path.join(os.path.dirname(__file__), "settings.yaml")

_cached_settings: Optional["NewsIntelligenceSettings"] = None


def _read_yaml(path: str) -> dict:
    try:
        import yaml  # PyYAML -- see requirements-news_intelligence.txt
    except ImportError as exc:
        raise ImportError(
            "PyYAML is required to load news_intelligence/config/settings.yaml. "
            "Install it with: pip install -r "
            "scripts/app/engines/news_intelligence/requirements-news_intelligence.txt"
        ) from exc

    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} did not parse to a mapping at the top level")
    return data


class _DictSettings:
    """Fallback used when pydantic is not installed. Same attribute-style
    access (`settings.cache.ttl_seconds`) as the pydantic model below, via
    a tiny recursive wrapper, so callers never need to know which backend
    is active."""

    def __init__(self, data: dict):
        for key, value in data.items():
            setattr(self, key, _DictSettings(value) if isinstance(value, dict) else value)
        self._raw = data

    def get(self, key: str, default: Any = None) -> Any:
        return self._raw.get(key, default)

    def __repr__(self) -> str:
        return f"_DictSettings({self._raw!r})"


def _build_pydantic_model():
    """Defines the pydantic schema lazily, only if pydantic is importable,
    so the module never fails to import on a system without it."""
    from pydantic import BaseModel, Field

    class RssFeed(BaseModel):
        name: str
        url: str

    class RssProviderConfig(BaseModel):
        enabled: bool = True
        timeout_seconds: float = 6.0
        retries: int = 1
        poll_interval_seconds: int = 90
        feeds: list[RssFeed] = Field(default_factory=list)

    class ProviderConfig(BaseModel):
        enabled: bool = True
        timeout_seconds: float = 8.0
        retries: int = 2
        poll_interval_seconds: int = 120

    class ProvidersConfig(BaseModel):
        nse: ProviderConfig = ProviderConfig()
        bse: ProviderConfig = ProviderConfig()
        rbi: ProviderConfig = ProviderConfig()
        sebi: ProviderConfig = ProviderConfig()
        rss: RssProviderConfig = RssProviderConfig()

    class CollectorConfig(BaseModel):
        max_items_per_cycle_per_provider: int = 200
        fan_out_workers: int = 6
        scheduler_default_interval_seconds: int = 120

    class DedupConfig(BaseModel):
        exact_hash_enabled: bool = True
        fuzzy_similarity_enabled: bool = True
        fuzzy_similarity_threshold: float = 0.88
        fuzzy_window_minutes: int = 180

    class CacheConfig(BaseModel):
        backend: str = "memory"
        ttl_seconds: int = 21600
        max_items: int = 20000
        cleanup_interval_seconds: int = 300
        thread_safe: bool = True

    class SentimentConfig(BaseModel):
        positive_threshold: float = 0.15
        very_positive_threshold: float = 0.55
        negative_threshold: float = -0.15
        very_negative_threshold: float = -0.55
        min_confidence: float = 0.2

    class ImpactConfig(BaseModel):
        very_high_score: float = 80
        high_score: float = 60
        medium_score: float = 35
        low_score: float = 15
        category_weights: dict[str, float] = Field(default_factory=dict)

    class MappingConfig(BaseModel):
        min_confidence: float = 0.5
        fuzzy_match_threshold: float = 0.82

    class LoggingConfig(BaseModel):
        channel: str = "news_intelligence"
        level: str = "INFO"

    class PerformanceConfig(BaseModel):
        async_enabled: bool = True
        max_concurrent_fetches: int = 8

    class NewsIntelligenceSettingsModel(BaseModel):
        providers: ProvidersConfig = ProvidersConfig()
        collector: CollectorConfig = CollectorConfig()
        dedup: DedupConfig = DedupConfig()
        cache: CacheConfig = CacheConfig()
        sentiment: SentimentConfig = SentimentConfig()
        impact: ImpactConfig = ImpactConfig()
        mapping: MappingConfig = MappingConfig()
        logging: LoggingConfig = LoggingConfig()
        performance: PerformanceConfig = PerformanceConfig()

    return NewsIntelligenceSettingsModel


# Public type alias -- either the pydantic model instance or the
# dict-fallback wrapper; both expose identical attribute access.
NewsIntelligenceSettings = Any


def _load(path: str = _SETTINGS_PATH) -> "NewsIntelligenceSettings":
    data = _read_yaml(path)
    try:
        ModelCls = _build_pydantic_model()
        settings = ModelCls(**data)
        _logger.info("NEWS_CONFIG loaded via pydantic validation")
        return settings
    except ImportError:
        _logger.warning(
            "NEWS_CONFIG pydantic not installed -- falling back to unvalidated "
            "dict-backed settings. Install pydantic for schema validation."
        )
        return _DictSettings(data)
    except Exception as exc:  # noqa: BLE001 -- validation error, still don't crash the caller
        _logger.error(f"NEWS_CONFIG validation failed ({exc}); falling back to raw dict settings")
        return _DictSettings(data)


def get_settings() -> "NewsIntelligenceSettings":
    """Process-cached settings singleton. Call reload_settings() to force
    a fresh read (tests do this after writing a temp settings file)."""
    global _cached_settings
    if _cached_settings is None:
        _cached_settings = _load()
    return _cached_settings


def reload_settings(path: str = _SETTINGS_PATH) -> "NewsIntelligenceSettings":
    global _cached_settings
    _cached_settings = _load(path)
    return _cached_settings
