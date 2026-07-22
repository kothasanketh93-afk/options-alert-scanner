"""
daily_cache.py
---------------
Generic "compute once before market open, read cheaply all day, expire
tomorrow" persistence for anything that isn't trade state. Used by:

  - instrument_master.py  (Phase 2: today's downloaded instrument master)
  - opportunity_score.py  (Phase 1: today's top-N shortlist)

WHY THIS EXISTS RATHER THAN A NEW CACHING MECHANISM: every GitHub
Actions run starts from a fresh container -- cache.py's in-process
TTLCache (used by upstox.py/angelone.py for their OWN instrument-master
fetch) does NOT survive between runs, only within one. state.py already
solved the "survive between runs" problem for trade state, by
committing a JSON file to a dedicated `state` git branch with
pull/push-retry handling for the race against heartbeat.yml. This
module reuses that exact mechanism (state.STATE_CLONE_DIR, the same
clone, the same branch, the same pull/push-retry helpers -- exposed as
state.safe_pull / state.git_identity / state.run_git) rather than
cloning the branch a second time or inventing a second persistence
path. If ensure_state_branch_checked_out() has already been called
this run (every entry point already does this before touching
state.py), this module has nothing extra to set up.

Branch layout (siblings of state.py's own state/<date>.json):
  <subdir>/<YYYY-MM-DD>.json       (opportunity shortlist -- small, plain JSON)
  <subdir>/<YYYY-MM-DD>.json.gz    (instrument master -- large, gzip-compressed)

Deliberately NOT a class wrapping global state (unlike cache.TTLCache,
which is one-per-process and fine to be an object) -- this is called
from several different scripts/processes in a day (build_opportunity_
shortlist.py, main.py, premarket_check.py), so a plain functions-plus-
explicit-subdir API is easier to reason about than an object whose
lifetime doesn't matter.
"""

import gzip
import json
import logging
from pathlib import Path

import state as state_mod

log = logging.getLogger("trading_system.daily_cache")


def _dir_for(subdir: str) -> Path:
    return state_mod.STATE_CLONE_DIR / subdir


def _json_path(subdir: str, date_str: str) -> Path:
    return _dir_for(subdir) / f"{date_str}.json"


def _gz_path(subdir: str, date_str: str) -> Path:
    return _dir_for(subdir) / f"{date_str}.json.gz"


def read(subdir: str, date_str: str, compressed: bool = False) -> dict | None:
    """
    Returns the cached payload for `date_str`, or None if nothing has
    been written yet today (a genuine cache-miss, not an error --
    callers decide what "no shortlist yet" / "no instrument master yet"
    means for them; see instrument_master.load_instrument_master()'s
    "compute it now" fallback and config.load_symbols_for_today()'s
    "fall back to the fixed watchlist" fallback for the two different
    ways callers handle this).

    Does NOT pull before reading -- the caller's entry point already
    called state.ensure_state_branch_checked_out() (which pulls) at
    startup; re-pulling on every read would just add git overhead for
    no benefit within a single run.
    """
    path = _gz_path(subdir, date_str) if compressed else _json_path(subdir, date_str)
    if not path.exists():
        return None
    try:
        if compressed:
            with gzip.open(path, "rt", encoding="utf-8") as f:
                return json.load(f)
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError, gzip.BadGzipFile, UnicodeDecodeError) as exc:
        # Same posture as state.load_state()'s corruption handling:
        # a bad cache file must never crash the run. Unlike trade
        # state there's nothing worth backing up here (this is
        # regenerable data, not today's trade history) -- just treat
        # it as a miss and let the caller recompute.
        log.error(
            "daily_cache: %s failed to read (%s: %s) -- treating as a "
            "cache miss, caller will recompute.",
            path, type(exc).__name__, exc,
        )
        return None


def write(subdir: str, date_str: str, payload: dict, commit_message: str,
          compressed: bool = False, max_retries: int = 3) -> bool:
    """
    Writes `payload` and commits+pushes it to the state branch,
    reusing state.py's exact pull-before-write / commit / push-retry
    loop (state.safe_pull / state.git_identity / state.run_git) so
    this shares state.py's already-battle-tested handling of the
    heartbeat.yml concurrent-writer race, rather than a second copy of
    that logic that could drift out of sync with it.

    Returns True on success, False if all retries were exhausted --
    deliberately does NOT raise (unlike state.save_state(), which
    raises on exhausted retries): losing today's opportunity shortlist
    or instrument-master cache is degraded, not fatal -- callers fall
    back to a safe default (see config.load_symbols_for_today() and
    instrument_master.load_instrument_master()) rather than the whole
    day's trading stopping. A raised git error here would otherwise
    take down premarket.yml's run for a problem that has a real,
    intentional fallback path.
    """
    path = _gz_path(subdir, date_str) if compressed else _json_path(subdir, date_str)
    path.parent.mkdir(parents=True, exist_ok=True)
    cwd = state_mod.STATE_CLONE_DIR

    for attempt in range(max_retries):
        state_mod.safe_pull(cwd)

        if compressed:
            with gzip.open(path, "wt", encoding="utf-8") as f:
                json.dump(payload, f)
        else:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, default=str)

        state_mod.run_git(["git", "add", "-A"], cwd=cwd)
        commit_result = state_mod.run_git(
            ["git", "commit", "-m", commit_message], cwd=cwd, check=False
        )
        if commit_result.returncode != 0:
            if "nothing to commit" in commit_result.stdout:
                return True  # no-op, cache already up to date
            log.error(
                "daily_cache: git commit failed for %s (attempt %d/%d): %s",
                path, attempt + 1, max_retries,
                (commit_result.stdout + commit_result.stderr).strip(),
            )
            continue  # retry from a fresh pull rather than raising -- see docstring

        push_result = state_mod.run_git(["git", "push", "origin", state_mod.STATE_BRANCH],
                                         cwd=cwd, check=False)
        if push_result.returncode == 0:
            return True
        # Push rejected -> retry with a fresh pull, same as state.save_state().

    log.error(
        "daily_cache: failed to push %s after %d attempts -- caller will "
        "fall back to its safe default for today.",
        path, max_retries,
    )
    return False


class DailyCache:
    """
    Thin object wrapper for callers that prefer it (opportunity_score.py,
    instrument_master.py both use this form) -- delegates to the module
    functions above, adds nothing of its own. `compressed=True` for
    large payloads (instrument master); False (default) for small ones
    (opportunity shortlist).
    """

    def __init__(self, subdir: str, compressed: bool = False):
        self._subdir = subdir
        self._compressed = compressed

    def read(self, date_str: str) -> dict | None:
        return read(self._subdir, date_str, compressed=self._compressed)

    def write(self, date_str: str, payload: dict, commit_message: str = None) -> bool:
        commit_message = commit_message or f"daily_cache: update {self._subdir}/{date_str}"
        return write(self._subdir, date_str, payload, commit_message,
                     compressed=self._compressed)

    def is_fresh(self, date_str: str) -> bool:
        return self.read(date_str) is not None
