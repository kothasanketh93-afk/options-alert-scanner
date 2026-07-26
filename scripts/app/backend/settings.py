"""
settings.py
-----------
Backend-only configuration, read from environment variables. Kept
separate from the top-level config.py on purpose -- config.py governs
scanner/trading behavior; this governs the read-only dashboard
process. Nothing here is imported by main.py or any engine.

Required environment variables (same names main.py/heartbeat_check.py
already use for the state branch, so no new secrets to provision):
  REPO_URL      -- e.g. https://github.com/you/repo.git
  GITHUB_TOKEN  -- token with at least read access to REPO_URL

Optional:
  DASHBOARD_STATE_REFRESH_SECONDS  -- min seconds between re-pulling
                                       the state branch (default 15)
  DASHBOARD_HOST                   -- bind host (default 127.0.0.1)
  DASHBOARD_PORT                   -- bind port (default 8800)
  DASHBOARD_HISTORY_LOOKBACK_DAYS  -- how many past state files
                                       /api/signals/history will scan
                                       by default (default 14)
  DASHBOARD_WS_BROADCAST_INTERVAL_SECONDS -- how often the Phase 3
                                       poll-and-diff loop re-checks
                                       for changes to push over
                                       /ws/dashboard (default: same
                                       as DASHBOARD_STATE_REFRESH_SECONDS
                                       -- polling faster than that
                                       buys nothing since state_reader
                                       itself won't re-pull the branch
                                       more often than that anyway)
"""

import os


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


REPO_URL = os.environ.get("REPO_URL")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")

STATE_REFRESH_SECONDS = _int_env("DASHBOARD_STATE_REFRESH_SECONDS", 15)
HOST = os.environ.get("DASHBOARD_HOST", "127.0.0.1")
PORT = _int_env("DASHBOARD_PORT", 8800)
HISTORY_LOOKBACK_DAYS = _int_env("DASHBOARD_HISTORY_LOOKBACK_DAYS", 14)

# Phase 3 (poll-and-diff WebSocket streaming) -- see
# services/live_service.py. Defaults to STATE_REFRESH_SECONDS itself
# since state_reader throttles actual git pulls to that interval
# regardless of how often it's called; polling more often than that
# would just re-check the same cached data.
WS_BROADCAST_INTERVAL_SECONDS = _int_env(
    "DASHBOARD_WS_BROADCAST_INTERVAL_SECONDS", STATE_REFRESH_SECONDS
)

# Part 10: max seconds a single Phase 3 poll cycle
# (live_service.compute_diff_events, run via asyncio.to_thread) is
# allowed to run before the watchdog abandons it and logs a CRITICAL
# warning, instead of poll_loop blocking forever on a hung git
# operation inside state_reader. Default of 30s is generous relative
# to a normal git pull + state read (typically well under 1s) while
# still catching a genuinely stuck call within a few poll cycles.
WS_CYCLE_TIMEOUT_SECONDS = _int_env("DASHBOARD_WS_CYCLE_TIMEOUT_SECONDS", 30)

# This process only ever needs read access to the state branch -- it
# must never be able to create/push it (that's main.py's job on its
# first-ever run). Passed straight through to
# state.ensure_state_branch_checked_out(allow_create=...).
ALLOW_STATE_BRANCH_CREATE = False
