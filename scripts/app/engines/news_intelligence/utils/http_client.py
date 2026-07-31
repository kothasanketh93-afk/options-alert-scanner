"""
app/engines/news_intelligence/utils/http_client.py -- shared HTTP fetch.
------------------------------------------------------------------------------
Deliberately does NOT import scripts/retry_utils.py: that module is written
for the broker (Angel One/Upstox) call shape and lives in a file that this
package stays independent of, same rationale
options_intelligence/engine.py's _days_to_expiry_safe() docstring gives for
not importing option_chain.days_to_expiry() -- this package must stay
importable/testable with zero broker/network dependency at import time.

Uses `requests` (already the project's one pinned HTTP dependency in
requirements.txt) with a small retry/backoff loop -- no new third-party
HTTP library added.
"""

from __future__ import annotations

import time
from typing import Optional

from logging_setup import get_logger

_logger = get_logger("news_intelligence")

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "*/*",
    # Added 2026-07-31 alongside the business-standard 403 investigation:
    # some CDN/anti-bot filters (Akamai, Cloudflare, etc.) also check for
    # Accept-Language and reject requests that omit it, even with an
    # otherwise-realistic User-Agent. BEST EFFORT ONLY -- verified live
    # via a non-CI fetcher that business-standard's feed itself is up
    # and returns valid RSS, so the 403 is specifically about how the
    # request is being made, not a dead URL. If this alone doesn't clear
    # it, the block is more likely IP-range-based (e.g. GitHub Actions'
    # published IP ranges are a common target for exactly this kind of
    # blocklisting) -- no header change fixes that; the fallback then is
    # dropping that one feed or fronting the request through a different
    # egress path, not further header tweaking.
    "Accept-Language": "en-US,en;q=0.9",
}


def fetch_text(url: str, timeout: float = 8.0, retries: int = 2,
                backoff_seconds: float = 1.5, headers: Optional[dict] = None) -> Optional[str]:
    """GET `url`, returning decoded text or None on total failure.
    Never raises -- providers must be able to fail individually without
    taking down the collector's fan-out over all providers (see
    collector/collector.py)."""
    import requests  # local import: keeps this helper's own import light for tests that stub it out

    merged_headers = dict(_DEFAULT_HEADERS)
    if headers:
        merged_headers.update(headers)

    last_error = None
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, headers=merged_headers, timeout=timeout)
            if resp.status_code == 200:
                return resp.text
            last_error = f"HTTP {resp.status_code}"
        except Exception as exc:  # noqa: BLE001 -- provider-level fetch must never raise
            last_error = str(exc)

        if attempt < retries:
            time.sleep(backoff_seconds * (attempt + 1))

    _logger.warning(f"NEWS_HTTP fetch failed url={url} error={last_error}")
    return None


def fetch_json(url: str, timeout: float = 8.0, retries: int = 2,
                headers: Optional[dict] = None) -> Optional[dict]:
    """Same contract as fetch_text but decodes JSON; returns None on any
    fetch OR decode failure."""
    import json

    text = fetch_text(url, timeout=timeout, retries=retries, headers=headers)
    if text is None:
        return None
    try:
        return json.loads(text)
    except Exception as exc:  # noqa: BLE001
        _logger.warning(f"NEWS_HTTP json decode failed url={url} error={exc}")
        return None
