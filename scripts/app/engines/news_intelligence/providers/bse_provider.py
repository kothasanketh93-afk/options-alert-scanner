"""
app/engines/news_intelligence/providers/bse_provider.py -- BSE India
corporate announcements (free, public, no API key).
"""

from __future__ import annotations

from logging_setup import get_logger
from ..utils.http_client import fetch_json
from .base_provider import BaseProvider

_logger = get_logger("news_intelligence")

# BSE's public announcements API -- returns today's corporate
# announcements across the exchange, no auth required.
_ANNOUNCEMENTS_URL = (
    "https://api.bseindia.com/BseIndiaAPI/api/AnnGetData/w?strCat=-1&strPrevDate="
    "&strScrip=&strSearch=P&strToDate=&strType=C"
)

_HEADERS = {
    "Accept": "application/json",
    "Referer": "https://www.bseindia.com/corporates/ann.html",
}


class BSEProvider(BaseProvider):
    name = "BSE"

    def fetch_raw(self) -> list[dict]:
        payload = fetch_json(
            _ANNOUNCEMENTS_URL,
            timeout=self._timeout(),
            retries=self._retries(),
            headers=_HEADERS,
        )
        if not payload:
            return []
        # Fix (live CI failure, 2026-07-28): BSE's API does not always
        # return a JSON object -- observed live response included a bare
        # JSON string (e.g. an error/notice body) instead of the usual
        # {"Table": [...]} shape. Calling .get() on that raised
        # AttributeError: 'str' object has no attribute 'get', which
        # collector.py's own try/except caught (so the run itself was
        # never at risk -- see collector.py's _run_one_provider() "never
        # raise" contract), but it silently zeroed out this provider for
        # the whole cycle. Now explicitly handles all three shapes BSE
        # can return: a dict with "Table", a bare list, or anything else
        # (str, int, None, ...) -- which degrades to an empty list
        # instead of raising.
        if isinstance(payload, dict):
            items = payload.get("Table", [])
        elif isinstance(payload, list):
            items = payload
        else:
            _logger.warning(f"NEWS_PROVIDER BSE unexpected payload type: {type(payload).__name__}")
            return []
        if not isinstance(items, list):
            _logger.warning("NEWS_PROVIDER BSE unexpected payload shape")
            return []
        return items
