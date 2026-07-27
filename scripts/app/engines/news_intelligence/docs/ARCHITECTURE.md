# Architecture

## Design principles

- **Independence.** Zero imports from `scripts/app/engines/decision_engine/`,
  `options_intelligence/`, `monitoring/`, `health/`, `dashboard/`,
  `telegram/`, `instrument_master.py`, `angelone.py`, `upstox.py`, or
  `config.py`. The only project-level shared module reused is
  `logging_setup.py` (for consistent log formatting/channel routing) and
  `requests` (already in root `requirements.txt`).
- **Strategy pattern for sources.** `providers/base_provider.py` defines
  one abstract contract (`fetch_raw() -> list[dict]`, never raises).
  `providers/__init__.py::get_enabled_providers()` is the single registry
  function that needs a one-line edit to add a new source.
- **Never raise across a fan-out boundary.** Every provider, every parser
  row, every mapper lookup is wrapped so one bad input/one failed source
  cannot abort the batch/cycle it's part of. See
  `collector/collector.py::_run_one_provider()`.
- **Direction vs. magnitude separation.** Sentiment carries direction
  (positive/negative). Impact is deliberately direction-agnostic --
  category + sentiment *magnitude* + mapping confidence. The adapter
  recombines both when producing the final signed `news_score`.
- **One facade.** `engine.py::NewsEngine` is the only class an external
  caller (or a future integration) needs to know about; every
  sub-package is independently testable/importable on its own.

## Data flow

```
 ┌────────────┐   raw dict    ┌──────────┐   NewsItem   ┌──────────┐
 │ providers/ │──────────────▶│ parser/  │─────────────▶│ mapper/  │
 └────────────┘                └──────────┘               └────┬─────┘
                                                                 │ enriched NewsItem
                                                                 ▼
                                                          ┌─────────────┐
                                                          │ collector/  │
                                                          │ (dedup)     │
                                                          └──────┬──────┘
                                                                 ▼
                                                          ┌─────────────┐
                                                          │   cache/    │
                                                          └──────┬──────┘
                                    on demand (get_news_score)   │
                              ┌──────────────┬───────────────────┘
                              ▼              ▼
                        ┌───────────┐  ┌───────────┐
                        │ sentiment/│  │  impact/  │
                        └─────┬─────┘  └─────┬─────┘
                              └───────┬────────┘
                                      ▼
                             ┌──────────────────┐
                             │ adapters/         │
                             │ decision_adapter  │
                             └──────────────────┘
```

Sentiment/impact are computed **on demand** in `get_news_score()`, not
eagerly at collection time -- both are pure, fast, in-process lexicon/math
operations (no I/O), so recomputing them per-query over a handful of
cached articles per symbol is cheaper than persisting a third
(sentiment, impact) tuple per cached item and keeping it in sync.

## Concurrency model

- **Collection**: `ThreadPoolExecutor` (not asyncio) in `collector.py`,
  since every provider ultimately calls the blocking `requests` library.
  `engine.collect_async()` wraps the same thread pool via
  `loop.run_in_executor()` for asyncio-based hosts.
- **Cache**: a single `threading.RLock` guards all mutation; reads
  re-check TTL and self-heal expired entries inline. Safe for concurrent
  collector threads and concurrent adapter reads.
- **Scheduler**: `collector/scheduler.py::NewsScheduler` is a plain daemon
  thread + `threading.Event` loop -- opt-in, independent of whether the
  host process is sync or async.

## Why not X?

- **Why not asyncio/aiohttp throughout?** The providers call `requests`
  (blocking); adopting `aiohttp` would mean rewriting every provider's
  HTTP call for no behavioral gain, when a thread pool already gives real
  concurrency for I/O-bound fetches. `collect_async()` still gives
  asyncio hosts a non-blocking entry point.
- **Why not a cloud/LLM sentiment API?** Explicitly out of scope
  ("No OpenAI API. No Claude API. No cloud AI."). The lexicon-based
  approach is deterministic, free, and fast enough for thousands of
  items/day.
- **Why not feedparser for RSS?** stdlib `xml.etree.ElementTree` covers
  RSS 2.0 and Atom cleanly in ~100 lines, avoiding one more third-party
  dependency for a well-understood, simple XML shape.
