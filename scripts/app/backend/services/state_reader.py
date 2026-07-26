"""
state_reader.py
----------------
The one place backend services touch state.py / the `state` git
branch. Every other service module goes through this -- never call
state.load_state() directly elsewhere in app/backend, so the
read-only contract and the refresh/caching policy stay in one spot.

Never calls state.save_state() or anything else that writes. Never
imports broker.py, auth.py, angelone.py, or upstox.py -- this process
has no broker credentials and doesn't need any.
"""

import logging
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import state as state_mod
from app.backend import settings

log = logging.getLogger("trading_system.backend.state_reader")

IST = timezone(timedelta(hours=5, minutes=30))

_lock = threading.Lock()
_last_pull_monotonic = None
_checkout_error = None


def _ensure_checked_out(force: bool = False) -> bool:
    """
    Re-pulls the state branch at most once per
    settings.STATE_REFRESH_SECONDS, shared across all requests via a
    lock so concurrent requests don't each trigger their own git
    fetch. Returns True if the branch is available to read from,
    False if checkout failed (e.g. missing REPO_URL/GITHUB_TOKEN, or
    the branch genuinely doesn't exist yet because trader.yml has
    never run).
    """
    global _last_pull_monotonic, _checkout_error

    if not settings.REPO_URL or not settings.GITHUB_TOKEN:
        _checkout_error = "REPO_URL and/or GITHUB_TOKEN not set for the dashboard process"
        return False

    with _lock:
        now = time.monotonic()
        if (
            not force
            and _last_pull_monotonic is not None
            and (now - _last_pull_monotonic) < settings.STATE_REFRESH_SECONDS
        ):
            return _checkout_error is None

        try:
            state_mod.ensure_state_branch_checked_out(
                settings.REPO_URL,
                settings.GITHUB_TOKEN,
                allow_create=settings.ALLOW_STATE_BRANCH_CREATE,
            )
            _checkout_error = None
        except Exception as exc:  # noqa: BLE001 -- surfaced via /api/health, not raised to callers
            _checkout_error = f"{type(exc).__name__}: {exc}"
            log.error("state branch checkout failed: %s", _checkout_error)
        _last_pull_monotonic = now
        return _checkout_error is None


def ensure_state_available() -> bool:
    """
    Public wrapper other services (replay_service, analytics_service)
    call before reading anything else off the same branch (daily_cache
    subdirs live alongside state/<date>.json on it) -- so they don't
    reach into this module's underscore-prefixed internals.
    """
    return _ensure_checked_out()


def checkout_status() -> dict:
    """Used by HealthService -- doesn't force a re-pull."""
    return {
        "ok": _checkout_error is None and _last_pull_monotonic is not None,
        "error": _checkout_error,
        "last_pull_ist": (
            datetime.fromtimestamp(
                time.time() - (time.monotonic() - _last_pull_monotonic), tz=IST
            ).isoformat()
            if _last_pull_monotonic is not None
            else None
        ),
    }


def get_state_for_date(date_str: str = None) -> dict:
    """
    Read-only equivalent of state.load_state(date_str). Returns
    state.py's own "fresh/empty" shape (never None) if the branch
    isn't reachable or the file doesn't exist yet, so callers don't
    need a separate not-found branch.
    """
    ok = _ensure_checked_out()
    date_str = date_str or state_mod.trading_date_str()
    if not ok:
        return state_mod._fresh_state(date_str)  # noqa: SLF001 -- intentional reuse of the same "empty day" shape
    return state_mod.load_state(date_str)


def list_available_dates(lookback_days: int = None) -> list:
    """
    Dates (most recent first) that have a real state file on the
    branch, within the lookback window. Used for /api/signals/history.
    """
    lookback_days = lookback_days or settings.HISTORY_LOOKBACK_DAYS
    ok = _ensure_checked_out()
    if not ok:
        return []

    state_dir = state_mod.STATE_CLONE_DIR / state_mod.STATE_DIR
    if not state_dir.exists():
        return []

    cutoff = state_mod.ist_now() - timedelta(days=lookback_days)
    dates = []
    for path in state_dir.glob("*.json"):
        stem = path.stem
        if "corrupted" in stem:
            continue
        try:
            file_date = datetime.strptime(stem, "%Y-%m-%d").replace(tzinfo=IST)
        except ValueError:
            continue
        if file_date >= cutoff:
            dates.append(stem)
    return sorted(dates, reverse=True)
