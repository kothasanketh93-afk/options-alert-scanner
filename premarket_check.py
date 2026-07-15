"""
premarket_check.py
-------------------
Runs ONCE per trading day from its OWN separate, lightweight workflow
(premarket.yml), scheduled at least 15 minutes before market open
(09:00 IST; market opens 09:15 IST). Purpose: catch a misconfiguration
or outage BEFORE the trading day starts, while there's still time to
fix it -- instead of discovering it only after trader.yml's first
09:30 run silently produces nothing, or heartbeat_check.py's first
alert lands 45+ minutes into the session.

Two outcomes on a day nothing should run, neither of which is an "issue":

  - Weekend/NSE holiday (holiday_guard.should_run() == False): skip
    silently, same as heartbeat_check.py -- nothing to check.
  - SKIP_TRADING_TODAY=true (auth.trading_enabled_today() == False):
    this is the user's own documented opt-out for the day (see
    auth.py's trading_enabled_today() docstring). Sends ONE short
    informational Telegram message confirming the opt-out was seen
    correctly, then exits -- this is expected, not a failure. Can also
    be triggered on demand any time via this workflow's
    workflow_dispatch button for an immediate confirmation, rather
    than waiting for the 09:00 IST schedule.

On an actual trading day, runs the full startup diagnostic:
  - config_validation.validate_startup_config(): every required
    secret is present
  - Angel One (primary) reachable: fresh TOTP login + one authenticated call
  - Upstox (fallback) reachable: token still valid
  - state branch writable: reuses heartbeat_check.py's real push-based
    check rather than reimplementing it
  - Telegram reachable: checked last, and independently of the checks
    above, so a Telegram outage is at least visible in the job log
    even though it also means the alert below can't be delivered

Any failure -> one Telegram alert naming exactly what's wrong, sent
with enough runway (>=15 min, by schedule) to fix it before trader.yml's
first run. All clear -> one short confirmation message, so silence is
never ambiguous between "everything's fine" and "this workflow didn't
run at all."
"""

import os
from datetime import datetime, timezone, timedelta

import auth
import config_validation
import holiday_guard
import state as state_mod
import telegram_notify as tg
from heartbeat_check import (
    check_angelone_reachable, check_upstox_reachable, check_telegram_reachable,
    push_status_marker,
)
from exceptions import ConfigurationError, TelegramFailure
from logging_setup import get_logger

IST = timezone(timedelta(hours=5, minutes=30))
log = get_logger("main")  # ops/pre-market surface, not a distinct trading channel


def main():
    now = state_mod.ist_now()

    if not holiday_guard.should_run():
        log.info("Non-trading day (weekend/NSE holiday) -- skipping pre-market check.")
        return

    if not auth.trading_enabled_today():
        log.info("SKIP_TRADING_TODAY=true -- trading opted out for today.")
        try:
            tg.send(
                "ℹ️ <b>Trading skipped today</b>\n\n"
                "SKIP_TRADING_TODAY is set to true, so no new trades will be "
                "scanned for today. This is expected if you chose to skip "
                "today -- set the repo variable back to false under repo "
                "Settings > Secrets and variables > Actions > Variables "
                "before market open on a day you want trading back on. You "
                "can also re-run this workflow manually any time for an "
                "immediate confirmation."
            )
        except TelegramFailure:
            log.error("Could not send the trading-skipped notice (Telegram unreachable).")
        return

    problems = []

    try:
        config_validation.validate_startup_config()
    except ConfigurationError as exc:
        problems.append(f"Startup config: {exc}")

    # Only attempt broker/state checks if the basic secrets are actually
    # there -- otherwise these just fail with the same missing-secret
    # error already captured above, adding noise instead of new
    # information.
    if not problems:
        angelone_ok, angelone_detail = check_angelone_reachable()
        if not angelone_ok:
            problems.append(f"Angel One (primary broker): {angelone_detail}")

        upstox_ok, upstox_detail = check_upstox_reachable()
        if not upstox_ok:
            problems.append(f"Upstox (fallback broker): {upstox_detail}")

        try:
            repo_url = os.environ["REPO_URL"]
            github_token = os.environ["GITHUB_TOKEN"]
            state_mod.ensure_state_branch_checked_out(repo_url, github_token)
            status = {"checked_at": now.isoformat(), "check": "premarket"}
            # Reuses heartbeat_check.py's real push-based writability
            # test (pull -> write -> commit -> push, with retries) rather
            # than reimplementing the same git-race handling here.
            # alerted_marker_action is a no-op: this run doesn't touch
            # heartbeat's own .heartbeat_alerted dedup marker.
            if not push_status_marker(status, now, lambda: None):
                problems.append(
                    "State branch is not writable (push failed) -- check repo "
                    "Settings > Actions > General > Workflow permissions."
                )
        except Exception as exc:
            problems.append(f"State branch check failed: {exc}")

    telegram_ok, telegram_detail = check_telegram_reachable()
    if not telegram_ok:
        # Can't reliably alert ABOUT Telegram being down via Telegram --
        # log it loudly instead, same as heartbeat_check.py does.
        log.error("Telegram unreachable: %s", telegram_detail)
        problems.append(f"Telegram unreachable: {telegram_detail}")

    if problems:
        text = (
            "🚨 <b>Pre-market check FAILED</b>\n\n" +
            "\n".join(f"- {p}" for p in problems) +
            f"\n\nChecked at {now.strftime('%H:%M')} IST -- market opens 09:15 IST. "
            f"Fix before then or today's trading may not run correctly."
        )
        try:
            tg.send(text)
        except TelegramFailure:
            log.error("Pre-market check found problems but the alert itself could "
                       "not be sent (Telegram unreachable): %s", problems)
    else:
        try:
            tg.send("✅ <b>Pre-market check passed</b> — all systems go for today.")
        except TelegramFailure:
            log.error("Pre-market check passed but the confirmation message could "
                       "not be sent (Telegram unreachable).")


if __name__ == "__main__":
    main()
