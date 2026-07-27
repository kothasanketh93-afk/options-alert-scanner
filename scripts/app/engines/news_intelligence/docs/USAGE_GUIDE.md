# Usage Guide

## Install

```bash
pip install -r scripts/app/engines/news_intelligence/requirements-news_intelligence.txt
```

## One-off collection + scoring

```python
from app.engines.news_intelligence import NewsEngine

engine = NewsEngine()
result = engine.collect()
print(result)  # <CollectionResult new=12 duplicates=3 errors={}>

score = engine.get_news_score("RELIANCE")
print(score)
# {'symbol': 'RELIANCE', 'news_score': 42.3, 'sentiment': 'positive',
#  'impact': 'high', 'confidence': 0.71, 'reasons': [...], 'article_count': 3,
#  'most_recent_headline': '...', 'most_recent_url': '...'}
```

## Analyzing a single headline without a full collection cycle

```python
from app.engines.news_intelligence.models.news import NewsItem, NewsCategory
from app.engines.news_intelligence.utils.time_utils import now_ist_iso

item = NewsItem(
    news_id="manual-1",
    headline="Company X reports record profit, beats estimates",
    summary="Strong quarterly performance driven by margin expansion.",
    source="MANUAL",
    published_at=now_ist_iso(),
    fetched_at=now_ist_iso(),
    category=NewsCategory.EARNINGS,
)
print(engine.analyze_item(item))
```

## Running collection on a schedule (cron-job.org style, matching the
project's existing trigger pattern)

```python
# a small script, triggered externally (e.g. cron-job.org -> GitHub Actions,
# same pattern the scanner itself already uses)
from app.engines.news_intelligence import NewsEngine

engine = NewsEngine()
engine.collect()
```

## Running collection as an in-process background loop instead

```python
engine = NewsEngine()
engine.start_background_collection(interval_seconds=120)
# ... host process does other work; news_intelligence keeps polling in a daemon thread ...
engine.stop_background_collection()
```

## Async host process

```python
import asyncio
from app.engines.news_intelligence import NewsEngine

async def main():
    engine = NewsEngine()
    await engine.collect_async()
    print(engine.get_news_score("TCS"))

asyncio.run(main())
```

## Inspecting recent cached news for a symbol

```python
engine.get_recent_news("HDFCBANK", limit=10)
# -> list of NewsItem.to_dict()
```

## Direct low-level use of a sub-package (no NewsEngine)

```python
from app.engines.news_intelligence.sentiment.analyzer import analyze_text

result = analyze_text("RBI cuts repo rate, markets rally sharply")
print(result.label, result.score, result.confidence)
```

## Enabling/disabling a provider

Edit `config/settings.yaml`:

```yaml
providers:
  sebi:
    enabled: false
```

then `engine.reload_config()` (or restart the process).
