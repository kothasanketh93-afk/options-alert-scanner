"""
retry_utils.py
--------------
One retry-with-backoff implementation, used by every outbound HTTP call
in the project (Upstox via upstox.py, Telegram via telegram_notify.py).
Before this, both files had their own nearly-identical retry loop --
duplicate code. Now there's exactly one, and the policy is centralized:

- Retry: connection errors, timeouts, HTTP 429, and momentary 5xx
  (500/502/503/504).
- Never retry: 401/403 (auth) -- raised immediately as
  AuthenticationError -- or any other 4xx, which retrying can't fix
  and which should fail loudly instead of burning the Actions job's
  timeout for no benefit. This exactly matches the original behavior
  in both files (resp.raise_for_status() on a non-retryable code
  raised immediately, uncaught, failing the job) -- just with a named
  exception instead of a bare HTTPError.
- On 429, honor the Retry-After header if the server sent one, instead
  of blindly using the exponential schedule -- the server is telling
  you exactly how long to wait, and guessing instead is worse for the
  project's "low API usage" goal, not better.
- Small random jitter added to every wait so that if multiple runs
  happen to retry at the same moment, they don't all retry in lockstep.
"""

import random
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import requests

from config import RETRY_MAX_ATTEMPTS, RETRY_BACKOFF_SECONDS, RETRYABLE_STATUS_CODES
from exceptions import AuthenticationError

AUTH_FAILURE_CODES = {401, 403}


def _parse_retry_after(resp):
    """Seconds to wait per a 429's Retry-After header (delta-seconds or
    HTTP-date form), or None if absent/unparseable."""
    header = resp.headers.get("Retry-After")
    if not header:
        return None
    try:
        return float(header)
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(header)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (dt - datetime.now(timezone.utc)).total_seconds())
    except (TypeError, ValueError):
        return None


def request_with_retry(method: str, url: str, *, error_cls=RuntimeError,
                        max_attempts: int = RETRY_MAX_ATTEMPTS,
                        base_backoff: float = RETRY_BACKOFF_SECONDS,
                        **kwargs):
    """
    Makes one HTTP request, retrying only transient failures.

    error_cls: exception raised for a non-retryable failure or after
    retries are exhausted (pass BrokerAPIError for Upstox calls,
    TelegramFailure for Telegram calls -- see upstox.py / telegram_notify.py).
    The raised exception is ALWAYS an instance of error_cls -- including
    on a 429. (BUGFIX: this used to unconditionally raise RateLimitError
    on a 429 regardless of error_cls, so a Telegram 429 raised a
    RateLimitError instead of a TelegramFailure -- callers doing
    `except TelegramFailure` never caught it, and it escaped uncaught
    past every per-event error boundary in main.py/heartbeat_check.py.)
    On a 429, the raised instance carries a `.retry_after` attribute
    (seconds, or None) duck-typed on regardless of error_cls, so a
    caller that wants it can read getattr(exc, "retry_after", None)
    without needing error_cls to specifically be RateLimitError.

    Raises AuthenticationError immediately, without retrying, on 401/403
    -- this one is NOT error_cls, on purpose: auth failures are a
    distinct, always-fatal category regardless of which API is being called.
    """
    last_exc = None
    retry_after = None
    for attempt in range(max_attempts):
        retry_after = None
        try:
            resp = requests.request(method, url, **kwargs)
        except requests.RequestException as exc:
            last_exc = error_cls(f"{method} {url} failed: {exc}")
        else:
            if resp.status_code in AUTH_FAILURE_CODES:
                # BUGFIX (2026-07-21): this used to discard the actual
                # response body and always say "token missing/expired/
                # invalid" -- a guess, not what the broker actually
                # said. Every OTHER status branch below already
                # includes resp.text[:200]; auth failures didn't, which
                # meant real live failures got debugged blind for days
                # (guessing TOTP/IP/MAC issues) with the real reason
                # sitting unread in the response body the whole time.
                raise AuthenticationError(
                    f"{resp.status_code} authenticating against {url} -- "
                    f"{resp.text[:300]}"
                )
            if resp.status_code == 429:
                retry_after = _parse_retry_after(resp)
                last_exc = error_cls(
                    f"429 rate limited by {url}" +
                    (f" (retry after {retry_after:.1f}s)" if retry_after is not None else "")
                )
                last_exc.retry_after = retry_after  # duck-typed: works for any error_cls
            elif resp.status_code in RETRYABLE_STATUS_CODES:
                last_exc = error_cls(f"{resp.status_code} from {url}")
            elif resp.status_code >= 400:
                raise error_cls(f"{resp.status_code} from {url}: {resp.text[:200]}")
            else:
                return resp

        if attempt < max_attempts - 1:
            if retry_after is not None:
                wait = retry_after
            else:
                wait = base_backoff * (2 ** attempt)
            wait += random.uniform(0, wait * 0.25)  # jitter: up to +25%
            time.sleep(wait)

    raise last_exc