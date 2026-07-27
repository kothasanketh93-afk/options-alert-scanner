"""
app/engines/news_intelligence/providers/nse_provider.py -- NSE India
corporate announcements (free, public, no API key).
------------------------------------------------------------------------------
NSE's public corporate-announcements JSON endpoint requires a warm session
cookie from nseindia.com's homepage before the API call succeeds (a well
known quirk of that site, not specific to this project) -- handled here via
a two-step fetch: GET the homepage first to obtain cookies, then GET the
announcements endpoint with those cookies attached. Both steps go through
utils/http_client's retry/timeout wrapper conceptually, but since this
provider needs a shared `requests.Session` (for the cookie jar) across both
calls, it manages its own session rather than using the stateless
fetch_json() helper directly.
"""

from __future__ import annotations

from logging_setup import get_logger
from .base_provider import BaseProvider

_logger = get_logger("news_intelligence")

_HOMEPAGE_URL = "https://www.nseindia.com"
_ANNOUNCEMENTS_URL = "https://www.nseindia.com/api/corporate-announcements?index=equities"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Referer": "https://www.nseindia.com/companies-listing/corporate-filings-announcements",
}


class NSEProvider(BaseProvider):
    name = "NSE"

    def fetch_raw(self) -> list[dict]:
        try:
            import requests

            session = requests.Session()
            session.headers.update(_HEADERS)
            timeout = self._timeout()

            # Step 1: warm the session (sets NSE's anti-bot cookies).
            session.get(_HOMEPAGE_URL, timeout=timeout)

            # Step 2: the actual announcements call.
            resp = session.get(_ANNOUNCEMENTS_URL, timeout=timeout)
            if resp.status_code != 200:
                _logger.warning(f"NEWS_PROVIDER NSE non-200 status={resp.status_code}")
                return []

            payload = resp.json()
            items = payload if isinstance(payload, list) else payload.get("data", [])
            return items or []
        except Exception as exc:  # noqa: BLE001 -- provider must never raise, see base_provider
            _logger.warning(f"NEWS_PROVIDER NSE fetch failed: {exc}")
            return []
