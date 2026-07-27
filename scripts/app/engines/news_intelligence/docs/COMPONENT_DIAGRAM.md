# Component Diagram

```
news_intelligence/
│
├── providers/            [Strategy: one class per free source]
│     base_provider.py    <<abstract>> fetch_raw()
│     nse_provider.py     NSEProvider
│     bse_provider.py     BSEProvider
│     rbi_provider.py     RBIProvider   (BeautifulSoup scrape)
│     sebi_provider.py    SEBIProvider  (BeautifulSoup scrape)
│     rss_provider.py     RSSProvider   (multi-feed, stdlib XML)
│
├── parser/                [raw dict -> NewsItem]
│     cleaner.py           text hygiene only
│     normalizer.py        per-provider field mapping + category guess
│     parser.py            parse_batch() / parse_one() entry points
│
├── mapper/                 [NewsItem -> companies/symbols/sectors]
│     symbol_mapper.py      seed table + exact/fuzzy match
│     sector_mapper.py      symbol -> sector lookup
│     company_mapper.py     orchestrator, returns MappingResult
│
├── sentiment/               [text -> SentimentResult]
│     keywords.py            lexicon (positive/negative/negation/intensifiers)
│     scorer.py               pure scoring math
│     analyzer.py              NewsItem-facing API
│
├── impact/                    [category+sentiment+mapping -> ImpactResult]
│     impact_engine.py          score/level/duration/urgency
│     confidence.py              impact confidence blend
│
├── cache/                       [thread-safe TTL store]
│     news_cache.py               NewsCache, get_default_cache()
│
├── collector/                     [orchestration]
│     collector.py                  collect_all() fan-out + dedup + cache
│     deduplicator.py                fuzzy near-duplicate detection
│     scheduler.py                    NewsScheduler (opt-in background loop)
│
├── adapters/                        [integration boundary, NOT wired in]
│     decision_adapter.py             NewsDecisionAdapter.get_news_score()
│
├── config/                            [YAML-driven settings]
│     settings.yaml
│     loader.py                        pydantic validation + fallback
│
├── utils/                               [shared stdlib-only helpers]
│     http_client.py, text_utils.py, time_utils.py
│
├── models/                                [typed contracts]
│     news.py, sentiment.py, impact.py
│
├── engine.py                                NewsEngine  <<facade>>
├── __init__.py                              exports NewsEngine
└── tests/                                    10 self-contained test files
```

## External dependency edges

```
news_intelligence/  ──uses──▶  logging_setup.py        (project-level, read-only use)
news_intelligence/  ──uses──▶  requests                 (already in root requirements.txt)
news_intelligence/  ──uses──▶  pyyaml, pydantic, bs4     (new, scoped to this module's
                                                            requirements-news_intelligence.txt)
news_intelligence/  ──NO edge─▶ decision_engine/, options_intelligence/,
                                 monitoring/, health/, dashboard/, telegram/,
                                 instrument_master.py, angelone.py, upstox.py, config.py
```
