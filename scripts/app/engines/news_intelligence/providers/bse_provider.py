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
        items = payload.get("Table", payload if isinstance(payload, list) else [])
        if not isinstance(items, list):
            _logger.warning("NEWS_PROVIDER BSE unexpected payload shape")
            return []
        return items
