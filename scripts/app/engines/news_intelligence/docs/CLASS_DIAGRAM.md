# Class Diagram (textual)

```
NewsEngine (engine.py)
    - settings
    - cache: NewsCache
    - _adapter: NewsDecisionAdapter
    - _scheduler: NewsScheduler | None
    + collect() -> CollectionResult
    + collect_async() -> CollectionResult
    + get_news_score(symbol) -> dict
    + analyze_item(item: NewsItem) -> dict
    + get_recent_news(symbol) -> list[dict]
    + start_background_collection(interval_seconds)
    + stop_background_collection()
    + reload_config()

BaseProvider (providers/base_provider.py)  <<abstract>>
    + name: str
    + fetch_raw() -> list[dict]           # abstract, never raises
    ▲
    ├── NSEProvider
    ├── BSEProvider
    ├── RBIProvider
    ├── SEBIProvider
    └── RSSProvider                        # multi-feed via config.providers.rss.feeds

NewsItem (models/news.py)                   @dataclass
    news_id, headline, summary, source, published_at, fetched_at,
    category: NewsCategory, companies, symbols, sectors, tags,
    url, description, raw_source_id, language

NewsCategory (models/news.py)                Enum(str)
    CORPORATE_ANNOUNCEMENT | EARNINGS | REGULATORY | MONETARY_POLICY |
    GOVERNMENT | MARKET_GENERAL | CORPORATE_ACTION | MERGER_ACQUISITION |
    LEGAL | UNKNOWN

SentimentResult (models/sentiment.py)         @dataclass
    label: SentimentLabel, score: float, confidence: float,
    reasons, positive_hits, negative_hits

SentimentLabel                                 Enum(str)
    VERY_POSITIVE | POSITIVE | NEUTRAL | NEGATIVE | VERY_NEGATIVE

ImpactResult (models/impact.py)                 @dataclass
    level: ImpactLevel, score: float, expected_duration, urgency,
    confidence: float, reasons

ImpactLevel                                      Enum(str)
    VERY_HIGH | HIGH | MEDIUM | LOW | IGNORE

MappingResult (mapper/company_mapper.py)          @dataclass
    companies, symbols, sectors, confidence

NewsCache (cache/news_cache.py)
    - _items: dict[str, (NewsItem, expires_at)]
    - _by_symbol: dict[str, set[str]]
    - _lock: threading.RLock
    + put(item, ttl_seconds=None) -> bool
    + get(news_id) -> NewsItem | None
    + contains(news_id) -> bool
    + get_by_symbol(symbol, limit) -> list[NewsItem]
    + all_active() -> list[NewsItem]
    + cleanup_expired() -> int
    + clear()

CollectionResult (collector/collector.py)
    new_items: list[NewsItem]
    duplicates_skipped: int
    provider_errors: dict[str, str]
    raw_counts: dict[str, int]

NewsScheduler (collector/scheduler.py)
    - _collect_fn: Callable
    - _thread: threading.Thread | None
    + start() / stop() / is_running()

NewsDecisionAdapter (adapters/decision_adapter.py)
    - _cache: NewsCache
    - _settings
    + get_news_score(symbol, lookback_articles=10) -> NewsScoreResult

NewsScoreResult (adapters/decision_adapter.py)     @dataclass
    symbol, news_score, sentiment, impact, confidence, reasons,
    article_count, most_recent_headline, most_recent_url
    + to_dict() -> dict
```

## Key relationships

- `NewsEngine` *has-a* `NewsCache`, *has-a* `NewsDecisionAdapter`,
  *optionally has-a* `NewsScheduler`.
- `collect_all()` *uses* `get_enabled_providers()` (returns
  `list[BaseProvider]`), *uses* `parse_batch()`, *uses*
  `map_news_item()`, *uses* `is_near_duplicate()`, *writes to*
  `NewsCache`.
- `NewsDecisionAdapter.get_news_score()` *reads from* `NewsCache`,
  *calls* `analyze_news_item()` and `compute_impact()` per article, then
  aggregates into one `NewsScoreResult`.
- Every `*Config` pydantic model in `config/loader.py` is a plain nested
  `BaseModel`; `_DictSettings` is the structural (duck-typed) fallback
  used when pydantic is not installed.
