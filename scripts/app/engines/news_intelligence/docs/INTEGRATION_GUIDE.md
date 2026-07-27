# Integration Guide (for a FUTURE change -- not done by this module)

This module is deliberately unwired. Nothing here has been connected to
the scanner, Decision Engine, Telegram, or dashboard. This document is
the checklist for whoever does that wiring later.

## 1. Install the extra dependencies

Either merge `requirements-news_intelligence.txt` into the root
`requirements.txt`, or keep it separate and `pip install -r` both in
deployment. Both are free/open-source (pyyaml, pydantic, beautifulsoup4).

## 2. Decide a collection trigger

Two options, both already supported:

- **External cron** (matches the project's existing cron-job.org + GitHub
  Actions pattern): add a small script/workflow step that constructs
  `NewsEngine()` and calls `.collect()`.
- **In-process scheduler**: call
  `engine.start_background_collection(interval_seconds=...)` once at
  process startup (e.g. alongside wherever the scanner's own main loop
  starts).

## 3. Wire the adapter into the Decision Engine

In `scripts/app/engines/decision_engine/` (NOT modified by this module),
add a call like:

```python
from app.engines.news_intelligence.adapters.decision_adapter import get_news_score

news_result = get_news_score(symbol)
# news_result: {'symbol', 'news_score', 'sentiment', 'impact', 'confidence', 'reasons', ...}
```

Treat `news_result` the same additive way `options_intelligence`'s output
is already folded into the Decision Engine's Standard Output Object --
i.e. add a `setup["news_intelligence"] = news_result` key, do not replace
any existing scoring input.

**Suggested weighting approach** (a starting point, not a requirement):
use `news_score` (`-100..100`) as one more signed input alongside the
existing OI/PCR/momentum signals, gated by `confidence` (e.g. ignore or
down-weight it below `confidence < 0.3`).

## 4. Wire Telegram (optional)

`NewsScoreResult.most_recent_headline` / `.most_recent_url` are ready to
drop straight into an existing Telegram message template -- no new
formatting logic needed for a first pass.

## 5. Swap the symbol table for the live instrument master (optional)

`mapper/symbol_mapper.py::load_symbol_table()` is the one function to
point at `scripts/instrument_master.py`'s live symbol list instead of the
~45-symbol seed table, if broader coverage is needed. This is the only
place a live-instrument-master import would be added, and it is
deliberately not done by this module (see that function's docstring).

## 6. Decide whether to persist the cache across restarts

Currently in-memory only (per `config.cache.backend: "memory"`). If
survival across process restarts is needed, the smallest change is
writing `NewsCache.all_active()` to disk/DB on shutdown and replaying it
into a fresh `NewsCache` (via `.put()`) on startup -- no changes to
`NewsCache`'s public interface are required for that.

## 7. Monitoring/Health Services hook (optional)

If the project's existing Health Services module wants a news_intelligence
health check, the natural probe is:

```python
engine.cache_size()             # sanity: is anything being collected at all?
result = engine.collect()
result.provider_errors          # dict of provider_name -> error string, empty if healthy
```

No changes to Health Services itself are made by this module -- this is
guidance for the follow-up change.

## Non-goals of this integration guide

- This module does **not** ship a REST API, so no new HTTP endpoint/route
  needs to be registered anywhere.
- This module does **not** modify `config.py`, so no existing
  feature-flag pattern needs to change shape -- a new
  `NEWS_INTELLIGENCE_ENABLED` flag (mirroring
  `OPTIONS_INTELLIGENCE_ENABLED`) could be added to `config.py` in the
  follow-up change, gating the Decision Engine's *call* to
  `get_news_score()`, not anything inside this module.
