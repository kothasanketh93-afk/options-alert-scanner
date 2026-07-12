"""
state.py
--------
Manages same-day trade state as a single JSON file committed to a
dedicated `state` git branch. No database, no long-term history.

State file path: state/<YYYY-MM-DD>.json  (IST trading date)

Design notes:
- One workflow run = one writer = no concurrent-write races.
- `concurrency.group` in the workflow yml queues overlapping runs so
  this assumption always holds even if a run overruns its schedule.
- Git push is the atomicity boundary: if push is rejected (should not
  normally happen given the concurrency group), we retry a fixed
  number of times with a fresh pull+rebase before giving up loudly.
"""

import json
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path

from config import MAX_CONSECUTIVE_SL, MAX_CONCURRENT_TRADES

IST = timezone(timedelta(hours=5, minutes=30))

STATE_BRANCH = "state"
STATE_DIR = "state"
REPO_ROOT = Path(__file__).resolve().parent.parent
STATE_CLONE_DIR = REPO_ROOT / ".state_branch"


def ist_now() -> datetime:
    return datetime.now(IST)


def trading_date_str(dt: datetime = None) -> str:
    dt = dt or ist_now()
    return dt.strftime("%Y-%m-%d")


def _run(cmd, cwd=None, check=True):
    return subprocess.run(
        cmd, cwd=cwd, check=check, capture_output=True, text=True
    )


def _git_identity(cwd):
    _run(["git", "config", "user.email", "trader-bot@local"], cwd=cwd)
    _run(["git", "config", "user.name", "Intraday Trader Bot"], cwd=cwd)


def ensure_state_branch_checked_out(repo_url: str, github_token: str):
    """
    Clones (or fast-updates) just the `state` branch into a sibling
    directory so we never touch the main branch's working tree.
    Creates the branch as an orphan if it doesn't exist yet.
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
            _run(["git", "pull", "origin", STATE_BRANCH], cwd=STATE_CLONE_DIR, check=False)
            return

    # Fresh clone attempt of just the state branch
    result = _run(
        ["git", "clone", "--branch", STATE_BRANCH, "--single-branch",
         auth_url, str(STATE_CLONE_DIR)],
        check=False,
    )
    if result.returncode != 0:
        # Branch doesn't exist yet -> create it as an orphan branch
        _run(["git", "clone", auth_url, str(STATE_CLONE_DIR)])
        _git_identity(STATE_CLONE_DIR)
        _run(["git", "checkout", "--orphan", STATE_BRANCH], cwd=STATE_CLONE_DIR)
        _run(["git", "rm", "-rf", "."], cwd=STATE_CLONE_DIR, check=False)
        (STATE_CLONE_DIR / ".gitkeep").write_text("")
        _run(["git", "add", ".gitkeep"], cwd=STATE_CLONE_DIR)
        _run(["git", "commit", "-m", "init state branch"], cwd=STATE_CLONE_DIR)
        _run(["git", "push", "-u", "origin", STATE_BRANCH], cwd=STATE_CLONE_DIR)

    _git_identity(STATE_CLONE_DIR)


def _state_file_path(date_str: str) -> Path:
    return STATE_CLONE_DIR / STATE_DIR / f"{date_str}.json"


def load_state(date_str: str = None) -> dict:
    date_str = date_str or trading_date_str()
    path = _state_file_path(date_str)
    if not path.exists():
        return {
            "date": date_str,
            "trades": {},          # trade_id -> trade dict
            "next_seq": {},        # "NIFTY-CE" -> int, for trade id numbering
            "summary_sent": False,
            "consecutive_sl_streak": 0,      # resets to 0 on any T1/T2 win
            "circuit_breaker_tripped": False,
            "circuit_breaker_notified": False,
            "oi_history": {},       # instrument_key -> last-seen OI, for build-up detection
        }
    with open(path, "r") as f:
        loaded = json.load(f)
    # Backfill fields for state files written before risk management
    # existed (e.g. mid-day deploy of this feature) so old runs don't KeyError.
    loaded.setdefault("consecutive_sl_streak", 0)
    loaded.setdefault("circuit_breaker_tripped", False)
    loaded.setdefault("circuit_breaker_notified", False)
    loaded.setdefault("oi_history", {})
    for trade in loaded.get("trades", {}).values():
        trade.setdefault("streak_counted", False)
        trade.setdefault("logged_to_rolling_log", False)
    return loaded


def save_state(state: dict, commit_message: str, max_retries: int = 3):
    date_str = state["date"]
    path = _state_file_path(date_str)
    path.parent.mkdir(parents=True, exist_ok=True)

    for attempt in range(max_retries):
        # Always pull latest before writing to minimize (already-rare) conflicts
        _run(["git", "pull", "origin", STATE_BRANCH], cwd=STATE_CLONE_DIR, check=False)

        with open(path, "w") as f:
            json.dump(state, f, indent=2, default=str)

        # `-A` so the rolling log (logs/rolling_log.jsonl) is picked up
        # too, not just today's state file.
        _run(["git", "add", "-A"], cwd=STATE_CLONE_DIR)
        commit_result = _run(
            ["git", "commit", "-m", commit_message], cwd=STATE_CLONE_DIR, check=False
        )
        if commit_result.returncode != 0 and "nothing to commit" in commit_result.stdout:
            return  # no-op, state unchanged

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
    Deletes state JSON files other than today's. Called once per run;
    cheap no-op most days. Keeps the state branch from growing forever
    even though git history itself will still retain old commits
    (acceptable: no *working-tree* accumulation, and you can periodically
    force-push a fresh orphan branch if you want to fully purge history).
    """
    state_dir = STATE_CLONE_DIR / STATE_DIR
    if not state_dir.exists():
        return
    removed_any = False
    for f in list(state_dir.glob("*.json"))[:max_files_to_scan]:
        if f.stem != keep_today:
            f.unlink()
            removed_any = True
    if removed_any:
        _run(["git", "add", "-A"], cwd=STATE_CLONE_DIR)
        _run(
            ["git", "commit", "-m", f"cleanup: keep only {keep_today}.json"],
            cwd=STATE_CLONE_DIR, check=False
        )


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
            and t["status"] in ("WAITING_ENTRY", "ACTIVE", "T1_HIT")
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
    return sum(
        1 for t in state["trades"].values()
        if t["status"] in ("WAITING_ENTRY", "ACTIVE", "T1_HIT")
    )


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
