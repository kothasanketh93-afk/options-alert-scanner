"""
app/engines/news_intelligence/utils/time_utils.py -- IST time helpers.
------------------------------------------------------------------------------
Reimplements the IST offset directly (timezone(timedelta(hours=5, minutes=30)))
rather than importing it from anywhere else in the project, same pattern
options_intelligence/engine.py already uses for its own IST constant --
this is a two-line stdlib constant, not business logic worth a cross-package
import.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from typing import Optional

IST = timezone(timedelta(hours=5, minutes=30))


def now_ist() -> datetime:
    return datetime.now(IST)


def now_ist_iso() -> str:
    return now_ist().isoformat()


def parse_to_ist_iso(value: str) -> Optional[str]:
    """Best-effort parse of a source-provided timestamp (RFC 2822 from RSS
    <pubDate>, ISO 8601 from NSE/BSE JSON, or a handful of common
    'DD-Mon-YYYY HH:MM:SS' style strings) into an IST-normalized ISO 8601
    string. Returns None (never raises) if nothing parses -- callers should
    fall back to fetched_at (see parser/normalizer.py)."""
    if not value:
        return None
    value = value.strip()

    # RFC 2822 (RSS <pubDate>, e.g. "Mon, 27 Jul 2026 09:15:00 +0530")
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(IST).isoformat()
    except Exception:
        pass

    # ISO 8601 variants
    iso_candidate = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(iso_candidate)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=IST)
        return dt.astimezone(IST).isoformat()
    except Exception:
        pass

    # Common NSE/BSE "DD-Mon-YYYY HH:MM:SS" style
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y"):
        try:
            dt = datetime.strptime(value, fmt).replace(tzinfo=IST)
            return dt.isoformat()
        except Exception:
            continue

    return None
