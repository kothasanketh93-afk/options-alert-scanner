# Configuration Guide

All tunables live in `config/settings.yaml`. Edit that file directly --
no environment variables or code changes needed for any of the settings
below.

## `providers.*`

Each of `nse`, `bse`, `rbi`, `sebi` supports:

| key                    | meaning                                    |
|-------------------------|--------------------------------------------|
| `enabled`               | include this source in `get_enabled_providers()` |
| `timeout_seconds`       | per-request HTTP timeout                   |
| `retries`               | retry attempts on failure (with backoff)   |
| `poll_interval_seconds` | informational -- how often you intend to poll (the scheduler uses `collector.scheduler_default_interval_seconds` unless overridden per call) |

`providers.rss` additionally has a `feeds:` list of `{name, url}` pairs.
Add a new free RSS feed by appending one entry -- no code change needed.

## `collector.*`

- `max_items_per_cycle_per_provider` -- caps how many raw rows one
  provider can contribute per cycle (protects against a runaway feed).
- `fan_out_workers` -- thread pool size for concurrent provider polling.
- `scheduler_default_interval_seconds` -- default period for
  `NewsEngine.start_background_collection()` if no interval is passed.

## `dedup.*`

- `exact_hash_enabled` -- toggle the free, always-on content-hash layer.
- `fuzzy_similarity_enabled` / `fuzzy_similarity_threshold` /
  `fuzzy_window_minutes` -- the near-duplicate layer in
  `collector/deduplicator.py`. Raise the threshold (closer to 1.0) to
  make it stricter (fewer things flagged as duplicates).

## `cache.*`

- `ttl_seconds` -- how long a cached NewsItem stays queryable.
- `max_items` -- hard cap; oldest items are evicted first (FIFO) once
  exceeded.
- `cleanup_interval_seconds` -- how often expired items are proactively
  swept (lazy expiry also happens on every `get()`).

## `sentiment.*`

Thresholds that convert a raw `-1..1` score into a `SentimentLabel`.
`min_confidence` is a floor applied whenever at least one lexicon phrase
was matched.

## `impact.*`

- `very_high_score` / `high_score` / `medium_score` / `low_score` --
  the `0..100` score cutoffs for `ImpactLevel`.
- `category_weights` -- per-`NewsCategory` baseline weight (`0..1`) that
  drives most of the impact score; raise a category's weight to make
  that KIND of news generally score higher impact regardless of
  sentiment strength.

## `mapping.*`

- `min_confidence` -- minimum `SymbolMatch` confidence to include a
  symbol in a `MappingResult`.
- `fuzzy_match_threshold` -- the difflib ratio threshold used by
  `symbol_mapper.find_symbols_in_text()`'s guarded fuzzy pass.

## `logging.*`

- `channel` -- the `logging_setup.get_logger()` channel name every
  module in this package logs under (default: `"news_intelligence"`).
- `level` -- informational; actual level is controlled by the project's
  existing `logging_setup.py` configuration.

## `performance.*`

- `async_enabled` -- informational flag; `NewsEngine.collect_async()` is
  always available regardless.
- `max_concurrent_fetches` -- reserved for future use if a provider ever
  needs finer-grained concurrency control than `collector.fan_out_workers`.

## Reloading at runtime

```python
engine.reload_config()   # NewsEngine method -- re-reads settings.yaml
```

or directly:

```python
from app.engines.news_intelligence.config import reload_settings
reload_settings()
```

## If PyYAML/pydantic aren't installed

- Without **PyYAML**: `get_settings()` raises `ImportError` with a clear
  message pointing at `requirements-news_intelligence.txt` -- this one is
  not optional, since there is no way to read the YAML file without it.
- Without **pydantic**: settings still load (via `_DictSettings`, a
  structural fallback with identical attribute access), just without
  schema validation/type coercion/defaults-filling. A warning is logged.
