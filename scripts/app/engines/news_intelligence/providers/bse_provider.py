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
            # BUGFIX (2026-08-23, live-log finding): BSE's API returns
            # the literal string "No Record Found!" (not an error, not
            # a maintenance notice -- this is BSE's own documented
            # response text for "your query matched zero announcements
            # today") every single cycle on days with genuinely no
            # matching announcements -- confirmed live at 59/59 cycles
            # on one full trading day, meaning this was almost
            # certainly being logged as a WARNING every 5 minutes for
            # entirely normal "nothing to report" behavior, not an
            # actual data-quality problem. That specific, known-benign
            # string is now recognized and degrades to [] SILENTLY
            # (matching what an empty {"Table": []} response already
            # does above) -- any OTHER unexpected string/int/None still
            # warns exactly as before, since those genuinely could
            # indicate a real problem (rate limiting, a changed API
            # shape, a maintenance page, etc.) that this fix does not
            # touch.
            if isinstance(payload, str) and payload.strip().lower() == "no record found!":
                return []
            # FIX (2026-07-31): logging only type(payload).__name__ meant
            # every occurrence looked identical in the logs with no way
            # to tell "BSE is serving a maintenance notice" apart from
            # "BSE is rate-limiting us" apart from anything else -- add a
            # short, truncated preview of the actual content so a future
            # read of this log line has something to diagnose from.
            # Still degrades to [] either way; this only changes what's
            # logged, not the fail-soft behavior above.
            preview = str(payload)[:200]
            _logger.warning(
                f"NEWS_PROVIDER BSE unexpected payload type: {type(payload).__name__} "
                f"preview={preview!r}"
            )
            return []
        if not isinstance(items, list):
            _logger.warning("NEWS_PROVIDER BSE unexpected payload shape")
            return []
        return items
