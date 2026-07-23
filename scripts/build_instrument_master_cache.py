"""
build_instrument_master_cache.py
----------------------------------
Phase 2 premarket entry point. Runs ONCE per trading day, from a new
step in premarket.yml (right alongside the existing startup
diagnostics premarket_check.py already runs) -- deliberately its OWN
thin script rather than folded into premarket_check.py, so a failure
here (or there) is attributable to the right subsystem in the job log
and in the Telegram alert.

What this does:
  1. Skip silently on a non-trading day or SKIP_TRADING_TODAY=true,
     same convention premarket_check.py already uses.
  2. Download today's instrument master (via whichever broker is
     currently primary -- gets failover for free) and persist it to
     the state branch via instrument_master.download_and_cache_
     instrument_master(), so every trader.yml run today reads the
     cache instead of re-downloading a multi-MB file every 5 minutes.
  3. Validate every symbol in config.SYMBOLS against the freshly
     cached master (instrument_master.validate_symbol()) and log a
     one-line reason for each -- exactly the "Reason for Skip" /
     "Reason for Success" the spec asks for.
  4. Send ONE short Telegram summary: how many symbols validated,
     which (if any) didn't and why.

FAILURE POSTURE: best-effort, not fatal. If the download or the state-
branch write fails, this script logs it and sends a warning Telegram
message but does NOT raise -- scanner.py's resolve_strike_candidates()
and option_chain.get_atm_strike() already fall back to the legacy
fixed-step arithmetic when instrument_master has nothing cached, and
instrument_master.load_instrument_master() itself falls back to a
same-run download if this step never ran. A failed premarket cache
build should degrade today's scanning, not stop it, and should
definitely not fail the whole premarket.yml workflow (which also runs
premarket_check.py's broker/state/Telegram reachability checks --
those should still get a clear pass/fail regardless of this step).
"""

import os
from datetime import datetime, timezone, timedelta

import auth
import holiday_guard
import instrument_master
import state as state_mod
import telegram_notify as tg
from config import SYMBOLS
from exceptions import TelegramFailure
from logging_setup import get_logger

IST = timezone(timedelta(hours=5, minutes=30))
log = get_logger("build_instrument_master_cache")


def main():
    now = datetime.now(IST)

    if not holiday_guard.should_run():
        log.info("Non-trading day (weekend/NSE holiday) -- skipping instrument master build.")
        return

    if not auth.trading_enabled_today():
        log.info("SKIP_TRADING_TODAY=true -- skipping instrument master build.")
        return

    try:
        repo_url = os.environ["REPO_URL"]
        github_token = os.environ["GITHUB_TOKEN"]
        state_mod.ensure_state_branch_checked_out(repo_url, github_token)
    except Exception as exc:
        log.error(f"Could not check out the state branch -- aborting cache build: {exc}", exc_info=True)
        _notify_best_effort(f"⚠️ Instrument master build FAILED: could not check out the "
                             f"state branch ({type(exc).__name__}: {exc}). Today's scan will "
                             f"fall back to the legacy fixed-step strike arithmetic.")
        return

    trading_date = instrument_master.today_str(now)

    try:
        rows = instrument_master.download_and_cache_instrument_master(trading_date)
    except Exception as exc:
        log.error(f"Instrument master download failed: {type(exc).__name__}: {exc}", exc_info=True)
        _notify_best_effort(f"⚠️ Instrument master download FAILED today "
                             f"({type(exc).__name__}: {exc}). Today's scan will fall back to "
                             f"the legacy fixed-step strike arithmetic for every symbol.")
        return

    ok_count, skip_lines = 0, []
    for symbol in SYMBOLS:
        try:
            ok, reason = instrument_master.validate_symbol(symbol, trading_date)
        except Exception as exc:
            ok, reason = False, f"{symbol}: validation raised {type(exc).__name__}: {exc}"
        if ok:
            ok_count += 1
            log.info(f"SUCCESS {reason}")
        else:
            skip_lines.append(reason)
            log.info(f"SKIP {reason}")

    summary = (
        f"📋 <b>Instrument master built for {trading_date}</b>\n"
        f"{len(rows)} rows downloaded. {ok_count}/{len(SYMBOLS)} configured symbols validated OK."
    )
    if skip_lines:
        summary += "\n\n<b>Skipped:</b>\n" + "\n".join(f"- {line}" for line in skip_lines[:20])
        if len(skip_lines) > 20:
            summary += f"\n... and {len(skip_lines) - 20} more (see job log)"
    _notify_best_effort(summary)


def _notify_best_effort(text: str):
    try:
        tg.send(text)
    except TelegramFailure:
        log.error("Could not send instrument-master build summary (Telegram unreachable): %s", text)


if __name__ == "__main__":
    main()
