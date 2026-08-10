"""
build_opportunity_shortlist.py
--------------------------------
Phase 1 premarket entry point. Runs ONCE per trading day from
premarket.yml, AFTER build_instrument_master_cache.py's step (needs
today's instrument master already cached -- see that script for why
it's a separate earlier step rather than folded into this one).

Same best-effort failure posture as build_instrument_master_cache.py:
logs and sends a warning Telegram message on failure, never raises --
config.refresh_symbols_for_today() already falls back to
FALLBACK_STOCK_WATCHLIST when this step hasn't produced anything for
today, so a failure here degrades today's stock scanning to the old
fixed-15-stock behavior rather than stopping the whole workflow.
"""

import os
from datetime import datetime, timezone, timedelta

import auth
import holiday_guard
import opportunity_score
import state as state_mod
import telegram_notify as tg
from daily_cache import DailyCache
from exceptions import TelegramFailure
from logging_setup import get_logger

IST = timezone(timedelta(hours=5, minutes=30))
log = get_logger("build_opportunity_shortlist")


def main():
    now = datetime.now(IST)

    if not holiday_guard.should_run():
        log.info("Non-trading day -- skipping opportunity shortlist build.")
        return

    if not auth.trading_enabled_today():
        log.info("SKIP_TRADING_TODAY=true -- skipping opportunity shortlist build.")
        return

    try:
        repo_url = os.environ["REPO_URL"]
        github_token = os.environ["GITHUB_TOKEN"]
        state_mod.ensure_state_branch_checked_out(repo_url, github_token)
    except Exception as exc:
        log.error(f"Could not check out the state branch -- aborting shortlist build: {exc}", exc_info=True)
        _notify_best_effort(f"⚠️ Opportunity shortlist build FAILED: could not check out the "
                             f"state branch ({type(exc).__name__}: {exc}). Today's stock scan "
                             f"will fall back to the fixed 15-stock watchlist.")
        return

    trading_date = now.strftime("%Y-%m-%d")

    try:
        payload = opportunity_score.build_shortlist(trading_date)
    except Exception as exc:
        log.error(f"Opportunity scoring failed: {type(exc).__name__}: {exc}", exc_info=True)
        _notify_best_effort(f"⚠️ Opportunity shortlist build FAILED today "
                             f"({type(exc).__name__}: {exc}). Today's stock scan will fall "
                             f"back to the fixed 15-stock watchlist.")
        return

    ok = DailyCache("opportunity").write(
        trading_date, payload,
        commit_message=f"opportunity_score: {trading_date} shortlist ({len(payload['shortlist'])} stocks)",
    )
    if not ok:
        log.error("Opportunity shortlist computed but FAILED to persist to the state branch -- "
                   "today's main.py runs will not see it and will fall back to the fixed watchlist.")
        _notify_best_effort("⚠️ Opportunity shortlist computed but could not be saved -- "
                             "today's scan will fall back to the fixed 15-stock watchlist.")
        return

    # BUGFIX (2026-08-10): the header below used to hardcode "Top 10:"
    # regardless of how many stocks were ACTUALLY shortlisted -- when
    # config's shortlist size is set below 10 (e.g. 5, as observed live:
    # "150 stocks scored, top 5 selected." immediately followed by a
    # "Top 10:" header with only 5 lines under it), the message
    # contradicted itself. top_lines is already correctly capped at
    # min(10, actual shortlist size) via the [:10] slice -- the header
    # just needs to describe that same count instead of a hardcoded 10.
    displayed_count = min(10, len(payload["shortlist"]))
    top_lines = "\n".join(f"{i+1}. {r['symbol']} : {r['score']}"
                           for i, r in enumerate(payload["shortlist"][:10]))
    summary = (
        f"🎯 <b>Opportunity shortlist built for {trading_date}</b>\n"
        f"{len(payload['all_scored'])} stocks scored, top {len(payload['shortlist'])} selected.\n\n"
        f"<b>Top {displayed_count}:</b>\n{top_lines}"
    )
    _notify_best_effort(summary)


def _notify_best_effort(text: str):
    try:
        tg.send(text)
    except TelegramFailure:
        log.error("Could not send opportunity-shortlist build summary (Telegram unreachable): %s", text)


if __name__ == "__main__":
    main()
