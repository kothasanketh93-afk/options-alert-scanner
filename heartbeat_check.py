"""
heartbeat_check.py
-------------------
Runs from its OWN separate, lightweight workflow (not the main
scanner+tracker one) so it can still detect and alert even if the main
workflow itself is stuck, failing silently, or not triggering at all.

Beyond the original "is the state branch getting fresh commits" check,
this now runs real health diagnostics each time it executes:

  - scanner_completed   : state branch got a trader commit recently (original check)
  - angelone_reachable  : Angel One (primary broker) login + one authenticated call
                           succeeds -- not necessarily urgent alone, since
                           broker_failover.py already falls back to Upstox
                           automatically, but surfaced so an extended primary
                           outage doesn't stay invisible on a day Upstox covers for it
  - upstox_reachable    : one authenticated Upstox (fallback broker) call succeeds
                           (also proves the token is valid -- a dead/expired
                           token fails this too)
  - telegram_reachable  : Telegram's getMe endpoint responds
  - state_branch_writable: this run's own status-marker commit+push actually
                           succeeds (a REAL test, not a proxy -- see below)

Only alerts when something is actually wrong, and only once per stale
period (existing marker-file logic, now folded into the status file).
"""

import os
from datetime import datetime, timezone, timedelta

import state as state_mod
import market_data
import auth
import telegram_notify as tg
import holiday_guard
from retry_utils import request_with_retry
from exceptions import AuthenticationError, BrokerAPIError, InstrumentNotFound, TelegramFailure, ConfigurationError
from logging_setup import get_logger
from config import (
    STALE_THRESHOLD_MIN, MARKET_OPEN_HHMM as MARKET_OPEN,
    MARKET_CLOSE_HHMM_HEARTBEAT as MARKET_CLOSE, STARTUP_GRACE_MIN,
)

IST = timezone(timedelta(hours=5, minutes=30))
log = get_logger("main")  # heartbeat is part of the ops/main surface, not a distinct trading channel


def _run(cmd, cwd=None):
    import subprocess
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def within_market_hours(now: datetime) -> bool:
    hhmm = (now.hour, now.minute)
    return MARKET_OPEN <= hhmm <= MARKET_CLOSE and now.weekday() < 5


def get_last_trader_commit_time() -> datetime:
    """
    Finds the last commit made by the TRADER workflow specifically
    (state.save_state's commits are messaged "update <date> @ <time>"),
    not just the latest commit on the branch overall -- otherwise
    heartbeat's own commits would make a dead trader workflow look
    "recently active."
    """
    result = _run(
        ["git", "log", "--grep=^update ", "-1", "--format=%cI"],
        cwd=state_mod.STATE_CLONE_DIR,
    )
    ts = result.stdout.strip()
    if not ts:
        return datetime(1970, 1, 1, tzinfo=IST)
    return datetime.fromisoformat(ts)


def check_upstox_reachable() -> tuple:
    """Also validates the token -- a dead/expired token fails this the
    same way an unreachable API would, which is the right behavior:
    Upstox is the FALLBACK broker now, so this specifically checks
    Upstox's own token is alive and ready, independent of whether
    Angel One (primary) is currently healthy -- using get_broker()
    here (the FailoverBroker) would silently succeed via Angel One
    even with a dead Upstox token, hiding exactly the failure this
    check exists to catch."""
    try:
        auth.validate_upstox_token(market_data.get_upstox_broker())
        return True, "ok"
    except AuthenticationError as exc:
        return False, f"authentication failed: {exc}"
    except (BrokerAPIError, ConfigurationError, InstrumentNotFound) as exc:
        # BUGFIX: InstrumentNotFound wasn't caught here before -- if
        # Upstox returned no LTP data for the probe instrument (e.g. a
        # transient data gap), get_ltp() raises InstrumentNotFound
        # uncaught, crashing heartbeat_check.py's own main() -- the
        # diagnostic tool failing on exactly the class of problem it
        # exists to report gracefully.
        return False, f"unreachable: {exc}"


def check_angelone_reachable() -> tuple:
    """Primary broker's own health check -- logs in fresh (Angel One
    has no persistent token to merely validate, unlike Upstox) and
    confirms a lightweight authenticated call succeeds. A failure here
    is expected to be transient/recoverable via failover during actual
    trading (broker_failover.py falls back to Upstox automatically) --
    this check exists so a persistent Angel One outage is VISIBLE
    (heartbeat alert) even on a day when Upstox is healthy and the
    trader workflow keeps running fine via fallback, masking the
    problem otherwise."""
    try:
        broker = market_data.get_angelone_broker()
        broker.login()
        broker.get_ltp("NSE|99926000")  # Nifty 50 index, confirmed token per Angel One's SmartAPI forum
        return True, "ok"
    except AuthenticationError as exc:
        return False, f"authentication failed: {exc}"
    except (BrokerAPIError, ConfigurationError, InstrumentNotFound) as exc:
        return False, f"unreachable: {exc}"


def check_telegram_reachable() -> tuple:
    try:
        bot_token, _ = auth.get_telegram_credentials()
        url = f"https://api.telegram.org/bot{bot_token}/getMe"
        request_with_retry("GET", url, error_cls=TelegramFailure, timeout=10, max_attempts=1)
        return True, "ok"
    except (TelegramFailure, ConfigurationError) as exc:
        return False, f"unreachable: {exc}"


def push_status_marker(status: dict, now: datetime, alerted_marker_action, max_retries: int = 3) -> bool:
    """
    Writes this run's diagnostic results to the state branch and
    pushes. This IS the state-branch-writable check, not a proxy for
    it -- if this push fails after retries, we genuinely cannot write
    to the state branch, which is exactly what needs to be known.
    Returns whether the push actually succeeded.

    alerted_marker_action: a zero-arg callable that applies (or
    re-applies) the .heartbeat_alerted write/unlink for this run.
    Called fresh on every retry attempt, same as the status file --
    both mutations must happen AFTER each pull, not once beforehand,
    so that a pull's hard-reset fallback (see _safe_pull in state.py)
    can never revert one of them out from under the other.

    trader.yml runs under a DIFFERENT concurrency group than
    heartbeat.yml, so the two workflows can genuinely run at the same
    time and both push to `state` -- a real, expected race, not an
    error condition. Structured exactly like state.py.save_state():
    pull first, THEN write/commit/push, retrying the whole cycle on a
    rejected push. Pulling before committing (rather than committing
    first and trying to reconcile after a rejected push) means there's
    never an unpushed local commit sitting around when a retry's pull
    runs -- important because on modern git, `git pull` with an
    unpushed local commit AND a diverged remote fails immediately with
    "Need to specify how to reconcile divergent branches" unless
    pull.rebase/pull.ff is configured, rather than auto-merging. A
    commit-first design would silently hit that on every retry and
    still end up reporting a false "push failed" -- exactly the false
    negative this whole function exists to eliminate, just one layer
    deeper. Reuses state.py's _safe_pull(), which also recovers from a
    genuine (rare) conflict by resetting to the remote tip.
    """
    import json
    status_path = state_mod.STATE_CLONE_DIR / ".heartbeat_status.json"
    push = None
    for attempt in range(max_retries):
        state_mod._safe_pull(state_mod.STATE_CLONE_DIR)

        status_path.write_text(json.dumps(status, indent=2, default=str))
        alerted_marker_action()
        _run(["git", "add", "-A"], cwd=state_mod.STATE_CLONE_DIR)
        commit = _run(["git", "commit", "-m", f"heartbeat: status @ {now.strftime('%H:%M:%S')}"],
                       cwd=state_mod.STATE_CLONE_DIR)
        if commit.returncode != 0 and "nothing to commit" not in (commit.stdout + commit.stderr):
            log.error("heartbeat status commit failed: %s", (commit.stdout + commit.stderr).strip())
            return False

        push = _run(["git", "push", "origin", "state"], cwd=state_mod.STATE_CLONE_DIR)
        if push.returncode == 0:
            return True
        if attempt < max_retries - 1:
            log.warning("heartbeat status push rejected (attempt %d/%d), likely a race with "
                        "trader.yml -- pulling and retrying: %s",
                        attempt + 1, max_retries, push.stderr.strip())

    log.error("heartbeat status push failed after %d attempts: %s",
               max_retries, push.stderr.strip() if push else "unknown error")
    return False


def main():
    repo_url = os.environ["REPO_URL"]
    github_token = os.environ["GITHUB_TOKEN"]

    now = state_mod.ist_now()
    if not within_market_hours(now):
        return

    if not holiday_guard.should_run():
        # Weekend or NSE holiday -- trader.yml never runs today (its own
        # holiday_guard step gates it), so state naturally goes stale.
        # Checking staleness here would be a guaranteed false alarm, not
        # a real signal. Upstox/Telegram reachability aren't checked
        # either: there's nothing for either to do on a non-trading day,
        # and it would just burn API calls for no diagnostic value.
        log.info("Non-trading day (weekend/NSE holiday) -- skipping heartbeat check.")
        return

    if not auth.trading_enabled_today():
        # SKIP_TRADING_TODAY=true -- main.py is deliberately skipping
        # today too (see auth.trading_enabled_today()'s docstring), so
        # the state branch legitimately won't get any trader commits
        # today. Same reasoning as the holiday skip above: checking
        # staleness or broker reachability here would be a guaranteed
        # false alarm, not a real signal, on a day the user opted out.
        log.info("SKIP_TRADING_TODAY=true -- trading opted out for today, "
                  "skipping heartbeat check.")
        return

    market_open_today = now.replace(hour=MARKET_OPEN[0], minute=MARKET_OPEN[1], second=0, microsecond=0)
    if (now - market_open_today).total_seconds() / 60 < STARTUP_GRACE_MIN:
        # Too early to judge staleness -- the trader workflow's first
        # commit of the day may not have landed yet.
        return

    state_mod.ensure_state_branch_checked_out(repo_url, github_token)
    last_commit = get_last_trader_commit_time()
    gap_min = (now - last_commit).total_seconds() / 60
    scanner_ok = gap_min < STALE_THRESHOLD_MIN

    angelone_ok, angelone_detail = check_angelone_reachable()
    upstox_ok, upstox_detail = check_upstox_reachable()
    telegram_ok, telegram_detail = check_telegram_reachable()

    problems = []
    if not scanner_ok:
        problems.append(f"Main workflow stale: no state update in {gap_min:.0f} min "
                         f"(threshold {STALE_THRESHOLD_MIN} min).")
    if not angelone_ok:
        # Not necessarily urgent on its own -- broker_failover.py falls
        # back to Upstox automatically, so the trader workflow may well
        # be running fine right now via fallback. This still surfaces
        # it (rather than staying silent) because an extended Angel One
        # outage would otherwise be invisible on any day Upstox happens
        # to be healthy -- see check_angelone_reachable()'s docstring.
        problems.append(f"Angel One (primary broker) problem: {angelone_detail}")
    if not upstox_ok:
        problems.append(f"Upstox (fallback broker) API/auth problem: {upstox_detail}")
    if not telegram_ok:
        # Can't reliably send a Telegram alert ABOUT Telegram being down --
        # this shows up in the job log / status marker instead.
        log.error("Telegram unreachable: %s", telegram_detail)
        problems.append(f"Telegram unreachable: {telegram_detail}")

    status = {
        "checked_at": now.isoformat(),
        "scanner_completed": {"ok": scanner_ok, "gap_minutes": round(gap_min, 1)},
        "angelone_reachable": {"ok": angelone_ok, "detail": angelone_detail},
        "upstox_reachable": {"ok": upstox_ok, "detail": upstox_detail},
        "telegram_reachable": {"ok": telegram_ok, "detail": telegram_detail},
    }

    marker = state_mod.STATE_CLONE_DIR / ".heartbeat_alerted"
    already_alerted = marker.exists()

    alert_text = None
    if problems:
        if not already_alerted:
            alert_text = (
                "HEARTBEAT WARNING\n\n" + "\n".join(f"- {p}" for p in problems) +
                f"\n\nLast trader update: {last_commit.isoformat()}"
            )

        def alerted_marker_action():
            marker.write_text(now.isoformat())
    elif already_alerted:
        def alerted_marker_action():
            marker.unlink(missing_ok=True)
    else:
        def alerted_marker_action():
            pass  # nothing to do -- no problems, and nothing was alerted before

    if alert_text is not None:
        try:
            tg.send(alert_text)
        except TelegramFailure:
            log.error("Could not send heartbeat alert -- Telegram itself unreachable.")

    if not push_status_marker(status, now, alerted_marker_action):
        log.error("state_branch_writable check FAILED this run -- see git error above. "
                   "This means even a healthy scanner/tracker run may not be able to "
                   "persist state, silently. Check repo Settings > Actions > General > "
                   "Workflow permissions if this persists.")


if __name__ == "__main__":
    main()
