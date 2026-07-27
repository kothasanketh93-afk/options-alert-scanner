# News Intelligence Engine (Part 11.5)

A completely independent module that collects free, public financial news
(NSE/BSE corporate announcements, RBI/SEBI press releases, free RSS
feeds), normalizes it, maps it to NSE symbols/sectors, scores its
sentiment and expected market impact with local (non-cloud) logic, and
exposes a standalone `NewsDecisionAdapter.get_news_score(symbol)` that the
existing Decision Engine can call *once wired in by a separate change*.

**This module does not modify any existing file and is not imported by
the scanner, Decision Engine, Options Intelligence, Monitoring, Health
Services, Dashboard, or Telegram code.** See `docs/INTEGRATION_GUIDE.md`
for how that wiring would happen later.

## Quick start

```python
from app.engines.news_intelligence import NewsEngine

engine = NewsEngine()
engine.collect()                        # one collection cycle, all providers
engine.get_news_score("RELIANCE")        # -> dict: news_score, sentiment, impact, confidence, reasons
```

Install this module's (small, free) extra dependencies:

```bash
pip install -r scripts/app/engines/news_intelligence/requirements-news_intelligence.txt
```

(`pyyaml`, `pydantic`, `beautifulsoup4` -- all free/open-source. The
project's root `requirements.txt` is untouched; `requests` is reused from
there.)

## Pipeline

```
providers/  --raw dict-->  parser/  --NewsItem-->  mapper/  --enriched NewsItem-->
    collector/ (dedup + cache) --> sentiment/ + impact/ (on demand) --> adapters/decision_adapter.py
```

1. **providers/** -- one free source per file (NSE, BSE, RBI, SEBI, RSS).
   Strategy pattern: `collector.py` fans out over whatever
   `get_enabled_providers()` returns; adding a source never touches an
   existing provider file.
2. **parser/** -- `cleaner.py` (text hygiene) + `normalizer.py`
   (per-provider field mapping) turn raw dicts into a single `NewsItem`
   shape (`models/news.py`).
3. **mapper/** -- `symbol_mapper.py` (seed NIFTY-constituent table + exact
   word-boundary and guarded-fuzzy matching), `sector_mapper.py`,
   orchestrated by `company_mapper.py`.
4. **collector/** -- `collector.py` fans out over providers with a thread
   pool, applies mapping, dedups (exact hash + fuzzy near-duplicate via
   `deduplicator.py`), and writes to `cache/news_cache.py`.
   `scheduler.py` is an optional in-process periodic-poll wrapper.
5. **sentiment/** -- pure local lexicon scoring (`keywords.py` +
   `scorer.py` + `analyzer.py`). No cloud AI call anywhere.
6. **impact/** -- `impact_engine.py` scores expected market impact
   (direction-agnostic magnitude) from category + sentiment magnitude +
   mapping confidence; `confidence.py` scores the Impact Engine's own
   certainty.
7. **cache/** -- thread-safe, TTL, symbol-indexed in-memory cache.
8. **adapters/decision_adapter.py** -- the one file that knows a Decision
   Engine exists elsewhere, and still doesn't import it. Returns a flat,
   JSON-serializable "Standard Output Object."
9. **engine.py** -- `NewsEngine`, the single facade (`collect()`,
   `collect_async()`, `get_news_score()`, `analyze_item()`,
   `start_background_collection()`).

## Configuration

All tunables live in `config/settings.yaml`, loaded and validated (via
pydantic, with a graceful dict-based fallback if pydantic isn't
installed) by `config/loader.py`. See `docs/CONFIG_GUIDE.md`.

## Testing

```bash
for f in scripts/app/engines/news_intelligence/tests/test_*.py; do python "$f"; done
```

Every test file is self-contained (own `sys.path` bootstrap, own local
`check()` helper) and runnable individually, matching the convention in
`app/engines/options_intelligence/tests/`. All tests use synthetic data
or mocked network calls -- no real HTTP requests are made during testing.

## Known limitations

- `mapper/symbol_mapper.py` ships a hand-curated ~45-symbol seed table
  (large-cap NIFTY/BANKNIFTY names), not the full NSE universe. See
  `load_symbol_table()`'s docstring for the intended override point.
- Conglomerate brand ambiguity (e.g. "HDFC" spanning HDFC Bank and HDFC
  Life, "Tata" spanning multiple Tata Group listings) can still under- or
  cross-match in edge cases; the fuzzy matcher requires 2+ independently
  matched distinctive tokens specifically to reduce this, but it isn't
  eliminated.
- RBI/SEBI providers scrape public listing pages (no official JSON API
  exists for either); markup drift on those sites could reduce their
  yield to zero until `_extract_rows()` is updated -- this fails closed
  (empty list, logged warning), never raises.
- Sentiment is lexicon-based, tuned for Indian financial-news phrasing --
  it is not a general-purpose or multilingual sentiment model.

## Full documentation

See `docs/`: `ARCHITECTURE.md`, `COMPONENT_DIAGRAM.md`, `CLASS_DIAGRAM.md`,
`CONFIG_GUIDE.md`, `USAGE_GUIDE.md`, `INTEGRATION_GUIDE.md`,
`PRODUCTION_READINESS.md`.
