"""
heartbeat_check.py
-------------------
Runs from its OWN separate, lightweight workflow (not the main
scanner+tracker one) so it can still detect and alert even if the main
workflow itself is stuck, failing silently, or not triggering at all.

Logic: during market hours, the state branch should get a new commit
roughly every 5 minutes. If the latest commit is older than
STALE_THRESHOLD_MIN, send one Telegram alert (and don't spam — only
alert once per stale period using a small marker file).
"""

import os
import subprocess
from datetime import datetime, timezone, timedelta

import state as state_mod
import telegram_notify as tg
from config import (
    STALE_THRESHOLD_MIN, MARKET_OPEN_HHMM as MARKET_OPEN,
    MARKET_CLOSE_HHMM_HEARTBEAT as MARKET_CLOSE, STARTUP_GRACE_MIN,
)

IST = timezone(timedelta(hours=5, minutes=30))


def _run(cmd, cwd=None):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def within_market_hours(now: datetime) -> bool:
    hhmm = (now.hour, now.minute)
    return MARKET_OPEN <= hhmm <= MARKET_CLOSE and now.weekday() < 5


def get_last_trader_commit_time() -> datetime:
    """
    Finds the last commit made by the TRADER workflow specifically
    (state.save_state's commits are messaged "update <date> @ <time>"),
    not just the latest commit on the branch overall.

    This matters because heartbeat_check.py itself commits to the same
    branch (its "heartbeat: mark alerted" / "heartbeat: clear alert
    marker" commits). If we naively looked at the single latest commit,
    the heartbeat's own writes would refresh the apparent last-activity
    timestamp on every check -- making a dead main workflow look
    "recently active" as soon as heartbeat sends its first alert, which
    would silence all further alerts even though the outage continues.
    Filtering to only the trader's commit message pattern avoids this.
    """
    result = _run(
        ["git", "log", "--grep=^update ", "-1", "--format=%cI"],
        cwd=state_mod.STATE_CLONE_DIR,
    )
    ts = result.stdout.strip()
    if not ts:
        # No trader commit found at all (e.g. very first run of the day,
        # or branch just created) -- treat as "never updated", which the
        # caller will correctly read as maximally stale.
        return datetime(1970, 1, 1, tzinfo=IST)
    return datetime.fromisoformat(ts)


def already_alerted_this_stale_period(marker_path) -> bool:
    return marker_path.exists()


def main():
    repo_url = os.environ["REPO_URL"]
    github_token = os.environ["GITHUB_TOKEN"]

    now = state_mod.ist_now()
    if not within_market_hours(now):
        return

    market_open_today = now.replace(hour=MARKET_OPEN[0], minute=MARKET_OPEN[1], second=0, microsecond=0)
    if (now - market_open_today).total_seconds() / 60 < STARTUP_GRACE_MIN:
        # Too early to judge staleness -- the trader workflow's first
        # commit of the day may not have landed yet, and comparing
        # against yesterday's last commit would falsely look stale.
        return

    state_mod.ensure_state_branch_checked_out(repo_url, github_token)
    last_commit = get_last_trader_commit_time()
    gap_min = (now - last_commit).total_seconds() / 60

    marker = state_mod.STATE_CLONE_DIR / ".heartbeat_alerted"

    if gap_min >= STALE_THRESHOLD_MIN:
        if not already_alerted_this_stale_period(marker):
            tg.send(
                f"HEARTBEAT WARNING\n\n"
                f"No state update in {gap_min:.0f} minutes during market "
                f"hours. Main scanner+tracker workflow may be stuck, "
                f"failing, or the Upstox token may have expired.\n\n"
                f"Last update: {last_commit.isoformat()}"
            )
            marker.write_text(now.isoformat())
            _run(["git", "add", "-A"], cwd=state_mod.STATE_CLONE_DIR)
            _run(["git", "commit", "-m", "heartbeat: mark alerted"], cwd=state_mod.STATE_CLONE_DIR)
            _run(["git", "push", "origin", "state"], cwd=state_mod.STATE_CLONE_DIR)
    else:
        if marker.exists():
            marker.unlink()
            _run(["git", "add", "-A"], cwd=state_mod.STATE_CLONE_DIR)
            _run(["git", "commit", "-m", "heartbeat: clear alert marker"], cwd=state_mod.STATE_CLONE_DIR)
            _run(["git", "push", "origin", "state"], cwd=state_mod.STATE_CLONE_DIR)


if __name__ == "__main__":
    main()
