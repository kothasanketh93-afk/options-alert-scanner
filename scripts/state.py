"""
state.py
--------
Manages same-day trade state as a single JSON file committed to a
dedicated `state` git branch. No database, no long-term history.

State file path: state/<YYYY-MM-DD>.json  (IST trading date)

Design notes:
- trader.yml is normally the only writer, but heartbeat.yml also
  commits a small status marker to this branch under its OWN
  concurrency group -- so it CAN genuinely run at the same time as
  trader.yml. This is a real, expected race, not a bug: both
  save_state() (below) and heartbeat_check.py's push_status_marker()
  handle it by pulling and retrying on a rejected push.
- Git push is the atomicity boundary: if push is rejected, we retry a
  fixed number of times with a fresh pull before giving up loudly.
"""

import json
import logging
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path

from config import MAX_CONSECUTIVE_SL, MAX_CONCURRENT_TRADES

log = logging.getLogger("trading_system.state")

IST = timezone(timedelta(hours=5, minutes=30))

STATE_BRANCH = "state"
STATE_DIR = "state"
REPO_ROOT = Path(__file__).resolve().parent.parent
STATE_CLONE_DIR = REPO_ROOT / ".state_branch"

# Statuses that count as a still-live/exposed position (not yet resolved).
# T1_HIT is included: a trade that hit T1 and is still tracking toward T2
# is still open, not terminal. Shared by has_open_trade() and
# count_open_trades() so the definition of "open" can't drift between them.
OPEN_STATUSES = ("WAITING_ENTRY", "ACTIVE", "T1_HIT")


def ist_now() -> datetime:
    return datetime.now(IST)


def trading_date_str(dt: datetime = None) -> str:
    dt = dt or ist_now()
    return dt.strftime("%Y-%m-%d")


def _run(cmd, cwd=None, check=True):
    return subprocess.run(
        cmd, cwd=cwd, check=check, capture_output=True, text=True
    )


def _safe_pull(cwd) -> bool:
    """
    Pulls the state branch and verifies it actually succeeded, instead
    of the previous check=False call whose result was never inspected --
    a failed or conflicted pull was silently ignored and the code
    proceeded to write/commit/push over an unknown working-tree state
    regardless. On failure, aborts any in-progress merge and hard-resets
    to the remote tip (safe here: called before any local write in both
    call sites below, so nothing local is lost). Returns True if the
    working tree is now clean and up to date with origin.
    """
    pull = _run(["git", "pull", "origin", STATE_BRANCH], cwd=cwd, check=False)
    if pull.returncode == 0:
        return True

    log.warning("git pull failed/conflicted (%s) -- resetting to origin/%s before continuing.",
                pull.stderr.strip(), STATE_BRANCH)
    _run(["git", "merge", "--abort"], cwd=cwd, check=False)
    _run(["git", "fetch", "origin", STATE_BRANCH], cwd=cwd, check=False)
    reset = _run(["git", "reset", "--hard", f"origin/{STATE_BRANCH}"], cwd=cwd, check=False)
    if reset.returncode != 0:
        log.error("git reset to origin/%s also failed: %s", STATE_BRANCH, reset.stderr.strip())
        return False
    return True


def _git_identity(cwd):
    _run(["git", "config", "user.email", "trader-bot@local"], cwd=cwd)
    _run(["git", "config", "user.name", "Intraday Trader Bot"], cwd=cwd)


def ensure_state_branch_checked_out(repo_url: str, github_token: str, allow_create: bool = True):
    """
    Clones (or fast-updates) just the `state` branch into a sibling
    directory so we never touch the main branch's working tree.
    Creates the branch as an orphan if it doesn't exist yet -- unless
    allow_create=False.

    allow_create=False is for any read-only caller whose workflow
    declares read-only permissions (contents: read) and never
    legitimately writes to the state branch. (BUGFIX: this function
    used to unconditionally push to create the branch if it didn't
    exist yet -- which would fail under those read-only permissions.
    Only matters once, before trader.yml's very first successful run,
    but it's a real mismatch between the declared permission model and
    what this shared code path actually does.) With allow_create=False
    and no branch yet, we just leave an empty local orphan branch,
    uncommitted and unpushed -- load_state() already treats a missing
    path as "no data yet," so the caller degrades gracefully.
    """
    STATE_CLONE_DIR.parent.mkdir(parents=True, exist_ok=True)
    auth_url = repo_url.replace(
        "https://", f"https://x-access-token:{github_token}@"
    )

    if STATE_CLONE_DIR.exists():
        _run(["git", "fetch", "origin", STATE_BRANCH], cwd=STATE_CLONE_DIR, check=False)
        result = _run(
            ["git", "checkout", STATE_BRANCH], cwd=STATE_CLONE_DIR, check=False
        )
        if result.returncode == 0:
            _safe_pull(STATE_CLONE_DIR)
            return

    # Fresh clone attempt of just the state branch
    result = _run(
        ["git", "clone", "--branch", STATE_BRANCH, "--single-branch",
         auth_url, str(STATE_CLONE_DIR)],
        check=False,
    )
    if result.returncode != 0:
        # Branch doesn't exist yet -> check out a local orphan branch
        # with no content, whether or not we're allowed to push it.
        _run(["git", "clone", auth_url, str(STATE_CLONE_DIR)])
        _git_identity(STATE_CLONE_DIR)
        _run(["git", "checkout", "--orphan", STATE_BRANCH], cwd=STATE_CLONE_DIR)
        _run(["git", "rm", "-rf", "."], cwd=STATE_CLONE_DIR, check=False)
        if allow_create:
            (STATE_CLONE_DIR / ".gitkeep").write_text("")
            _run(["git", "add", ".gitkeep"], cwd=STATE_CLONE_DIR)
            _run(["git", "commit", "-m", "init state branch"], cwd=STATE_CLONE_DIR)
            _run(["git", "push", "-u", "origin", STATE_BRANCH], cwd=STATE_CLONE_DIR)
        # else: leave it local-only -- see docstring above.

    _git_identity(STATE_CLONE_DIR)


def _state_file_path(date_str: str) -> Path:
    return STATE_CLONE_DIR / STATE_DIR / f"{date_str}.json"


def load_state(date_str: str = None) -> dict:
    date_str = date_str or trading_date_str()
    path = _state_file_path(date_str)
    if not path.exists():
        return _fresh_state(date_str)

    try:
        with open(path, "r") as f:
            loaded = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        # Corrupted state file (partial write, disk issue, whatever) --
        # crashing here means the ENTIRE run stops, including the
        # tracker step for trades that might still be open in a state
        # file we can't even read. Recovering with a fresh state for
        # today is strictly better than that: back up the unreadable
        # file (never delete it -- it might be manually recoverable)
        # and continue with a clean slate. This does mean any trades
        # tracked so far today are lost from the running state, which
        # is why the "_recovered_from_corruption" marker below exists --
        # main.py checks it and sends a Telegram alert so you know this
        # happened instead of silently continuing.
        backup_path = path.with_suffix(f".corrupted-{datetime.now(IST).strftime('%H%M%S')}.json")
        try:
            path.rename(backup_path)
        except OSError:
            pass  # backup best-effort; don't let a backup failure block recovery
        logging.getLogger("trading_system.state").error(
            "State file %s failed to parse (%s: %s) -- backed up to %s, "
            "starting fresh state for %s.",
            path, type(exc).__name__, exc, backup_path, date_str,
        )
        fresh = _fresh_state(date_str)
        fresh["_recovered_from_corruption"] = True
        return fresh

    # Backfill fields for state files written before risk management
    # existed (e.g. mid-day deploy of this feature) so old runs don't KeyError.
    loaded.setdefault("consecutive_sl_streak", 0)
    loaded.setdefault("circuit_breaker_tripped", False)
    loaded.setdefault("circuit_breaker_notified", False)
    loaded.setdefault("oi_history", {})
    loaded.setdefault("next_display_num", len(loaded.get("trades", {})))
    for trade in loaded.get("trades", {}).values():
        trade.setdefault("streak_counted", False)
        trade.setdefault("display_label", trade.get("trade_id", "Trade ?"))
    return loaded


def _fresh_state(date_str: str) -> dict:
    return {
        "date": date_str,
        "trades": {},          # trade_id -> trade dict
        "next_seq": {},        # "NIFTY-CE" -> int, for trade id numbering
        "next_display_num": 0,  # simple sequential counter for "Trade N" Telegram labels
        "summary_sent": False,
        "consecutive_sl_streak": 0,      # resets to 0 on any T1/T2 win
        "circuit_breaker_tripped": False,
        "circuit_breaker_notified": False,
        "oi_history": {},       # instrument_key -> last-seen OI, for build-up detection
    }


def save_state(state: dict, commit_message: str, max_retries: int = 3):
    date_str = state["date"]
    path = _state_file_path(date_str)
    path.parent.mkdir(parents=True, exist_ok=True)

    for attempt in range(max_retries):
        # Always pull latest before writing to minimize conflicts (the
        # only genuine concurrent writer is heartbeat.yml's small status
        # marker, which touches different files, but verify rather than
        # assume it went cleanly).
        _safe_pull(STATE_CLONE_DIR)

        with open(path, "w") as f:
            json.dump(state, f, indent=2, default=str)

        # `-A` so any deletions from cleanup_old_state_files() are
        # picked up too, not just today's state file -- one atomic
        # commit for both, called immediately after each other every
        # run.
        _run(["git", "add", "-A"], cwd=STATE_CLONE_DIR)
        commit_result = _run(
            ["git", "commit", "-m", commit_message], cwd=STATE_CLONE_DIR, check=False
        )
        if commit_result.returncode != 0:
            if "nothing to commit" in commit_result.stdout:
                return  # no-op, state unchanged
            # BUGFIX: a commit failure for any OTHER reason (permissions,
            # git identity, disk issue) used to silently fall through to
            # the push below. Since there was nothing new to push, `git
            # push` succeeds trivially ("Everything up-to-date"), and this
            # function returned as if the save had succeeded -- while this
            # run's actual state changes were never committed, silently
            # lost, with the next run loading stale state and nothing in
            # the job log to explain why. Must raise here, not fall through.
            raise RuntimeError(
                f"git commit failed for a reason other than 'nothing to "
                f"commit' (attempt {attempt + 1}/{max_retries}): "
                f"{(commit_result.stdout + commit_result.stderr).strip()}"
            )

        push_result = _run(
            ["git", "push", "origin", STATE_BRANCH], cwd=STATE_CLONE_DIR, check=False
        )
        if push_result.returncode == 0:
            return
        # Push rejected -> someone else wrote concurrently (shouldn't happen
        # given the workflow concurrency group, but handle it defensively).

    raise RuntimeError(
        f"Failed to push state after {max_retries} attempts. "
        f"Check for a stuck/duplicate workflow run."
    )


def cleanup_old_state_files(keep_today: str, max_files_to_scan: int = 30):
    """
    Deletes state JSON files other than today's from disk. Called once
    per run, right before save_state() -- deliberately does NOT commit
    or push here: save_state()'s own `git add -A` + single commit +
    push picks these deletions up together with the day's state write
    and the rolling log, as one atomic commit. (A separate premature
    commit here previously could be silently discarded if save_state()'s
    pull-conflict recovery had to hard-reset to origin before its own
    commit -- bundling into one commit removes that risk entirely: if a
    reset happens, these deletions simply haven't been committed yet and
    get naturally redone by next run's cleanup pass.)

    Corrupted-state backups (load_state()'s ".corrupted-HHMMSS.json"
    files) are deliberately excluded: they exist specifically so a
    corrupted day can be manually recovered later, so cleanup must never
    remove them. A plain "*.json" glob would also match those backups
    (they end in .json too), so we only touch files that look like a
    genuine dated state file (YYYY-MM-DD.json, no ".corrupted-" marker).
    """
    state_dir = STATE_CLONE_DIR / STATE_DIR
    if not state_dir.exists():
        return
    # BUGFIX: filtering out ".corrupted-" backups used to happen AFTER
    # slicing to max_files_to_scan -- glob()'s ordering is arbitrary, so
    # if corrupted backups ever accumulated faster than genuine old
    # files, they could crowd the slice and leave real old state files
    # uncleaned indefinitely. Filter first, then slice.
    candidates = [f for f in state_dir.glob("*.json") if ".corrupted-" not in f.name]
    for f in candidates[:max_files_to_scan]:
        if f.stem != keep_today:
            f.unlink()


def next_trade_id(state: dict, symbol: str, direction: str, moneyness: str, alert_dt: datetime) -> str:
    """
    Format: SYMBOL-DIRECTION-MONEYNESS-YYYYMMDD-HHMM-SEQ
    e.g. NIFTY-CE-OTM-20260713-0940-01
    SEQ increments per symbol+direction+moneyness+day so IDs stay short
    and readable, and ATM/ITM/OTM alerts never collide on the same ID.
    """
    key = f"{symbol}-{direction}-{moneyness}"
    seq = state["next_seq"].get(key, 0) + 1
    state["next_seq"][key] = seq
    date_part = alert_dt.strftime("%Y%m%d")
    time_part = alert_dt.strftime("%H%M")
    return f"{symbol}-{direction}-{moneyness}-{date_part}-{time_part}-{seq:02d}"


def next_display_number(state: dict) -> int:
    """
    Simple 1/2/3... counter for the "Trade N" label shown in Telegram
    messages, added 2026-07-21 because the full trade_id
    (SYMBOL-DIRECTION-MONEYNESS-YYYYMMDD-HHMM-SEQ) was confusing to
    read/re-find across a trade's several follow-up messages (entry
    activated, T1, T2, SL). Separate from next_trade_id()'s per-
    symbol+direction+moneyness counter -- this one just counts up
    once per NEW alert for the whole day, in the order alerts fire.
    """
    state["next_display_num"] = state.get("next_display_num", 0) + 1
    return state["next_display_num"]


def has_open_trade(state: dict, symbol: str, direction: str) -> bool:
    """
    V2.0: suppression is per symbol+direction ONLY (spec item 4 --
    "Only one active trade per Symbol, Direction"). This is stricter
    than V1's per-moneyness suppression, and is consistent with V2's
    scanner already reducing ATM/ITM/OTM down to a single best strike
    per direction before returning a setup -- there's no longer a
    legitimate case for two simultaneously-open trades on the same
    symbol+direction regardless of moneyness.

    "Open" includes T1_HIT, not just WAITING_ENTRY/ACTIVE -- a trade
    that hit T1 and is still tracking toward T2 (tracker.py's status
    label) is still a live position, not a resolved one.
    """
    for t in state["trades"].values():
        if (
            t["symbol"] == symbol
            and t["direction"] == direction
            and t["status"] in OPEN_STATUSES
        ):
            return True
    return False


def count_open_trades(state: dict) -> int:
    """
    WAITING_ENTRY + ACTIVE + T1_HIT combined, across all
    symbols/directions/moneyness. T1_HIT is included because a trade
    that hit T1 and is still tracking toward T2 is still exposed --
    it's tracker.py's status label for "not yet resolved", not a
    terminal state.
    """
    return sum(1 for t in state["trades"].values() if t["status"] in OPEN_STATUSES)


def concurrent_limit_reached(state: dict, max_concurrent: int = MAX_CONCURRENT_TRADES) -> bool:
    return count_open_trades(state) >= max_concurrent


def record_trade_outcome_for_streak(state: dict, final_status: str):
    """
    Call once when a trade reaches a terminal outcome. SL_HIT /
    T1_THEN_SL extend the losing streak; T2_HIT (a clean full win)
    resets it. AMBIGUOUS / NOT_ENTERED / EOD_CLOSED / T1_HIT_THEN_EOD /
    T1_HIT_THEN_AMBIGUOUS are deliberately excluded — they're not clean
    losses and shouldn't move the streak either direction.
    """
    if final_status in ("SL_HIT", "T1_THEN_SL"):
        state["consecutive_sl_streak"] += 1
        if state["consecutive_sl_streak"] >= MAX_CONSECUTIVE_SL:
            state["circuit_breaker_tripped"] = True
    elif final_status == "T2_HIT":
        state["consecutive_sl_streak"] = 0


def circuit_breaker_active(state: dict) -> bool:
    return state.get("circuit_breaker_tripped", False)


# ---------------------------------------------------------------------
# Public aliases for daily_cache.py (Phase 1/2 instrument-master and
# opportunity-shortlist persistence). daily_cache.py reuses this
# module's git-clone/pull/push machinery instead of re-implementing it
# -- these three names were previously private (leading underscore);
# exposed here, unchanged in behavior, so a second module can share the
# exact same `state` branch clone/pull/retry logic rather than cloning
# the branch a second time or duplicating the retry loop.
# ---------------------------------------------------------------------
safe_pull = _safe_pull
git_identity = _git_identity
run_git = _run
