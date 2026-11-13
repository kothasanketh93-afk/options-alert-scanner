"""Archive of the 1-minute option candles every tracker processed (added 2026-11-13).

Why: without the candles, questions like "did the stop-loss only get hit by a wick?", "was the entry really never touched?" and
"what did the premium do after the exit?" cannot be answered from the ledger. Candles are keyed by instrument (many strategies
share one option, so each candle is stored once) and written per day, gzip-compressed, next to the ledgers on the state branch.

record() is called from tracker.process_trade_candles (pure in-memory); flush() merges the buffer into the day file. Both are
best-effort: the archive must never be able to break tracking.
"""
import gzip
import json
from pathlib import Path

_BUF = {}      # {instrument_key: {"HH:MM": [o, h, l, c]}}  (date is the file's)


def record(instrument_key, candles):
    try:
        if not instrument_key:
            return
        d = _BUF.setdefault(instrument_key, {})
        for c in candles:
            ts = str(c["timestamp"])
            d[ts[11:16]] = [c["open"], c["high"], c["low"], c["close"]]
    except Exception:
        pass


def flush(path):
    """Merge the in-memory buffer into `path` (a .json.gz file) and clear it. Returns candles written, 0 on any failure."""
    try:
        if not _BUF:
            return 0
        p = Path(path)
        data = {}
        if p.exists():
            try:
                with gzip.open(p, "rt", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = {}            # unreadable old file: start over rather than lose today's candles
        n = 0
        for key, rows in _BUF.items():
            data.setdefault(key, {}).update(rows)
            n += len(rows)
        p.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(p, "wt", encoding="utf-8") as f:
            json.dump(data, f, separators=(",", ":"))
        _BUF.clear()
        return n
    except Exception:
        return 0
