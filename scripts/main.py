"""
main.py
-------
Single entry point run by the GitHub Actions workflow (trader.yml) on
a phased schedule: every 5 minutes during the two active-scanning
windows, every 15 minutes during the 11:35-13:45 IST lunch gap (see
README.md's "GitHub Actions minutes budget" section, and
trader.yml's own comments, for why).
Order of operations (see architecture notes for why this must be one
workflow / one writer):

  1. Load today's state from the `state` git branch
  2. Run scanner -> append any new WAITING_ENTRY trades (respecting
     the same-symbol+direction suppression rule)
  3. For every non-terminal trade, fetch missing 1-min candles and
     advance it through tracker.process_trade_candles()
  4. Send Telegram messages for new events (idempotent via
     trade["messages_sent"])
  5. Around 15:25 IST, send the daily summary once
  6. Save state back to the branch, cleanup old files
"""

import sys
import os
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(__file__))

import state as state_mod
import tracker
import telegram_notify as tg
import market_data
import scanner
import auth
import config_validation
from exceptions import TelegramFailure, BrokerAPIError
from logging_setup import get_logger
import config
from config import SUMMARY_HHMM

IST = timezone(timedelta(hours=5, minutes=30))
TERMINAL_STATUSES = {"T2_HIT", "SL_HIT", "NOT_ENTERED", "AMBIGUOUS", "EOD_CLOSED"}
log = get_logger("main")


def new_trade_record(setup: dict, trade_id: str, alert_dt: datetime, display_label: str) -> dict:
    return {
        "trade_id": trade_id,
        "display_label": display_label,  # short "Trade N" label for Telegram messages -- see state.next_display_number()
        "symbol": setup["symbol"],
        "direction": setup["direction"],
        "moneyness": setup["moneyness"],
        "instrument_key": setup["instrument_key"],
        "strike": setup["strike"],
        "expiry": setup["expiry"],
        "spot_price": setup["spot_price"],
        "alert_time": alert_dt.isoformat(),
        "alert_premium": setup["alert_premium"],
        "entry_low": setup["entry_low"],
        "entry_high": setup["entry_high"],
        "stop_loss": setup["stop_loss"],
        "target_1": setup["target_1"],
        "target_2": setup["target_2"],
        "session": setup["session"],

        "grade": setup["grade"],
        # Phase 3: numeric Trade Score Engine fields -- shown in Telegram
        # per an explicit decision reversing the earlier V2.0 "no raw
        # numeric score ever reaches Telegram" design (see trade_score.py).
        "trade_score": setup["trade_score"],
        "probability_pct": setup["probability_pct"],
        "confidence_score": setup["confidence_score"],
        "risk_score": setup["risk_score"],
        "reward_score": setup["reward_score"],
        "expected_rr": setup["expected_rr"],
        "factor_checklist": setup["factor_checklist"],
        # Backward-compat display fields, still carried too:
        "confidence_stars": setup["confidence_stars"],
        "confidence_label": setup["confidence_label"],
        "risk_rating": setup["risk_rating"],
        "risk_label": setup["risk_label"],
        "expected_holding_minutes": list(setup["expected_holding_minutes"]),
        "why_reasons": setup["why_reasons"],
        "alternatives": setup.get("alternatives", []),

        "status": "WAITING_ENTRY",
        "final_status": None,
        "actual_entry_time": None,
        "tracked_entry_price": None,
        "highest_since_entry": None,
        "lowest_since_entry": None,
        "max_favorable_pct": 0.0,
        "max_adverse_pct": 0.0,
        "t1_hit": False,
        "t1_hit_time": None,
        "t2_hit": False,
        "t2_hit_time": None,
        "sl_hit_time": None,
        "eod_last_price": None,
        "last_processed_ts": alert_dt.isoformat(),

        "messages_sent": [],  # e.g. ["NEW_TRADE_ALERT", "ENTRY_ACTIVATED"]
        "streak_counted": False,
    }


def notify_once(trade: dict, event_key: str, message_fn, *args):
    if event_key in trade["messages_sent"]:
        return
    text = message_fn(*args)
    tg.send(text)
    trade["messages_sent"].append(event_key)


def run_scanner_step(state: dict, now: datetime):
    # Circuit breaker: if MAX_CONSECUTIVE_SL losses have hit today,
    # stop generating new alerts for the rest of the day. Already-open
    # trades are still tracked normally by run_tracker_step -- this
    # only blocks NEW setups.
    if state_mod.circuit_breaker_active(state):
        if not state.get("circuit_breaker_notified"):
            try:
                tg.send(tg.fmt_circuit_breaker(state_mod.MAX_CONSECUTIVE_SL))
                state["circuit_breaker_notified"] = True
            except TelegramFailure:
                # Leave circuit_breaker_notified False so this is retried
                # next run instead of silently never being sent.
                log.error("Circuit breaker tripped but the Telegram alert "
                           "could not be sent -- will retry next run.")
        return

    ranked = scanner.detect_all_setups(state["oi_history"])

    chosen = None
    for candidate in ranked:
        if state_mod.has_open_trade(state, candidate["symbol"], candidate["direction"]):
            # This candidate's symbol+direction already has a live trade
            # -- skip it and fall through to the next-best candidate
            # rather than alerting nothing for the whole run just
            # because the single top-ranked pick happened to conflict.
            continue
        chosen = candidate
        break

    if chosen is None:
        return  # every passing candidate conflicted with an open trade, or none passed the gate

    if state_mod.concurrent_limit_reached(state):
        # Cap hit -- skip silently (not worth a Telegram message per
        # missed setup; the daily summary shows total recommendations
        # so nothing is hidden, just capped).
        return

    # BUGFIX (live feedback, 2026-07-21): `ranked` can contain the SAME
    # symbol+direction up to 3 times (once per ITM/ATM/OTM bucket --
    # detect_setups() keeps the best strike from each bucket
    # separately). Listing all three as "alternatives" isn't 3
    # distinct opportunities, just 3 strike choices for the same
    # underlying signal -- confusing noise, not real alternatives.
    # `ranked` is already best-to-worst (rank_all()), so the first
    # occurrence of each (symbol, direction) pair seen here is already
    # its best-scoring moneyness; keep only that one per pair.
    seen_symbol_direction = {(chosen["symbol"], chosen["direction"])}
    deduped_alternatives = []
    for c in ranked:
        if c is chosen:
            continue
        key = (c["symbol"], c["direction"])
        if key in seen_symbol_direction:
            continue
        seen_symbol_direction.add(key)
        deduped_alternatives.append(c)

    chosen["alternatives"] = [
        {
            "symbol": c["symbol"], "direction": c["direction"], "moneyness": c["moneyness"],
            "grade": c["grade"], "trade_score": c["trade_score"],
        }
        for c in deduped_alternatives
    ]

    trade_id = state_mod.next_trade_id(
        state, chosen["symbol"], chosen["direction"], chosen["moneyness"], now
    )
    display_label = f"Trade {state_mod.next_display_number(state)}"
    trade = new_trade_record(chosen, trade_id, now, display_label)
    state["trades"][trade_id] = trade

    try:
        notify_once(trade, "NEW_TRADE_ALERT", tg.fmt_new_alert, trade)
    except TelegramFailure:
        # The trade is already recorded in state -- it will still be
        # tracked normally by run_tracker_step. Only the initial alert
        # is missed; log loudly so it's visible in the job log rather
        # than crashing the run and losing tracker-step progress too.
        log.error("New trade %s created but the NEW_TRADE_ALERT could not be "
                   "sent (Telegram failure). Trade will still be tracked.", trade_id)


def _dispatch_tracker_events(trade: dict, events: list):
    """Sends the Telegram message for each tracker event. Raises
    TelegramFailure if a send fails after retries -- caller
    (run_tracker_step) is responsible for containing that per-trade so
    one trade's Telegram outage doesn't block every other trade."""
    for event in events:
        etype = event["type"]
        if etype == "ENTRY_ACTIVATED":
            notify_once(trade, "ENTRY_ACTIVATED", tg.fmt_entry_activated, trade)
        elif etype == "T1_HIT":
            mins = tracker.compute_time_taken(trade["actual_entry_time"], trade["t1_hit_time"])
            notify_once(trade, "T1_HIT", tg.fmt_t1_hit, trade, mins)
        elif etype == "T2_HIT":
            mins = tracker.compute_time_taken(trade["actual_entry_time"], trade["t2_hit_time"])
            notify_once(trade, "T2_HIT", tg.fmt_t2_hit, trade, mins)
        elif etype == "SL_HIT":
            mins = tracker.compute_time_taken(trade["actual_entry_time"], trade["sl_hit_time"])
            notify_once(trade, "SL_HIT", tg.fmt_sl_hit, trade, mins)
        elif etype == "NOT_ENTERED":
            notify_once(trade, "NOT_ENTERED", tg.fmt_not_entered, trade)
        elif etype in ("EOD_CLOSED",):
            notify_once(trade, "EOD_CLOSED", tg.fmt_eod_closed, trade)
        elif etype in ("AMBIGUOUS", "AMBIGUOUS_AFTER_T1"):
            notify_once(trade, "AMBIGUOUS", tg.fmt_ambiguous, trade, event["candle"])


def run_tracker_step(state: dict, now: datetime):
    for trade_id, trade in state["trades"].items():
        if trade["status"] in TERMINAL_STATUSES:
            continue

        try:
            candles = market_data.get_1min_candles_since(
                trade["instrument_key"], trade["last_processed_ts"]
            )
            if not candles:
                continue

            events = tracker.process_trade_candles(trade, candles)
            _dispatch_tracker_events(trade, events)

            if trade["status"] in TERMINAL_STATUSES and not trade["streak_counted"]:
                state_mod.record_trade_outcome_for_streak(state, trade.get("final_status"))
                trade["streak_counted"] = True

        except (BrokerAPIError, TelegramFailure) as exc:
            # BUGFIX: this used to also catch the bare TradingSystemError
            # base class, which includes AuthenticationError -- so a dead/
            # expired Upstox token degraded into a silently-logged failure
            # repeated for every open trade, every single run, with
            # trader.yml still reporting SUCCESS despite doing zero real
            # tracking work. Only network/broker/Telegram failures are
            # contained per-trade here now; AuthenticationError (and any
            # other unexpected TradingSystemError) propagates up to
            # main()'s outer handler, which logs it, saves whatever
            # progress was made, and re-raises -- so the job fails loudly
            # and visibly instead of quietly doing nothing.
            # Contain the failure to THIS trade. tracker.process_trade_candles()
            # already mutated `trade` in place (status/last_processed_ts) even
            # if the failure happened afterwards (e.g. a Telegram send for one
            # of its events) -- that partial progress is real and should still
            # be saved for every OTHER trade in state["trades"], not thrown
            # away. Known residual gap: if a candle batch produced multiple
            # events and only the first send succeeded, the later event(s) for
            # THIS trade won't be retried next run, since last_processed_ts has
            # already moved past the candle that generated them. This is rare
            # (requires multiple events in one batch) and is a deliberate
            # trade-off against the much more common/severe failure mode this
            # fixes: one bad trade or one Telegram blip no longer discards
            # every other trade's progress for the whole run.
            log.error("Trade %s: tracker step failed (%s: %s) -- continuing with "
                       "remaining trades; this trade will be retried next run.",
                       trade_id, type(exc).__name__, exc)


def maybe_send_daily_summary(state: dict, now: datetime):
    hhmm = (now.hour, now.minute)
    if hhmm < SUMMARY_HHMM or state.get("summary_sent"):
        return
    trades = list(state["trades"].values())
    if not trades:
        return
    text = tg.fmt_daily_summary(state["date"], trades)
    try:
        tg.send(text)
        state["summary_sent"] = True
    except TelegramFailure:
        # Leave summary_sent False so it's retried next run instead of
        # silently never being sent for the day.
        log.error("Daily summary could not be sent (Telegram failure) -- will retry next run.")


def main():
    config_validation.validate_startup_config()

    if not auth.trading_enabled_today():
        # SKIP_TRADING_TODAY=true -- the user's documented daily opt-out
        # (see auth.trading_enabled_today()'s docstring). Exit cleanly
        # with no alert: this is an expected, deliberate skip, not a
        # failure, and premarket_check.py already sends its own
        # confirmation message earlier in the morning for this case.
        log.info("SKIP_TRADING_TODAY=true -- trading opted out for today, skipping run.")
        return

    repo_url = os.environ["REPO_URL"]           # e.g. https://github.com/you/repo.git
    github_token = auth.get_github_token()

    now = state_mod.ist_now()
    date_str = state_mod.trading_date_str(now)

    state_mod.ensure_state_branch_checked_out(repo_url, github_token)

    # Phase 1 (opportunity_score.py): swap SYMBOLS/STOCK_SYMBOLS from
    # the safe import-time fallback (config.FALLBACK_STOCK_WATCHLIST)
    # to today's real opportunity shortlist, now that the state branch
    # is actually checked out and daily_cache.py has something to
    # read. MUST happen before run_scanner_step() below -- see
    # config.refresh_symbols_for_today()'s docstring for why this
    # can't just happen at `import config` time.
    config.refresh_symbols_for_today()

    state = state_mod.load_state(date_str)

    if state.pop("_recovered_from_corruption", False):
        log.error("State file was corrupted and recovered fresh for %s -- "
                   "any trades tracked earlier today were lost from state.", date_str)
        try:
            tg.send(
                f"⚠️ <b>State Recovery</b>\n\n"
                f"Today's state file ({date_str}) was corrupted and unreadable. "
                f"Recovered with a fresh state — the corrupted file was backed up "
                f"in the state branch. Any trades tracked earlier today are no "
                f"longer in state (Telegram alerts already sent for them are "
                f"unaffected, but the tracker will not resume tracking them)."
            )
        except TelegramFailure:
            log.error("Could not send state-recovery alert (Telegram failure) -- continuing anyway.")

    # Any exception below is logged and re-raised AFTER state is saved, not
    # instead of saving it -- run_scanner_step/run_tracker_step already
    # contain their own per-trade/per-message failures (see above), so
    # reaching this except is for genuinely unexpected errors (a bug, an
    # unimplemented scanner stub, etc). Without this, such an error would
    # skip save_state() entirely and discard the whole run's progress,
    # including any Telegram messages already sent earlier in the run --
    # causing them to be re-sent as duplicates next run.
    run_error = None
    try:
        run_scanner_step(state, now)
        run_tracker_step(state, now)
        maybe_send_daily_summary(state, now)
    except Exception as exc:
        run_error = exc
        log.error("Unexpected error during run (%s: %s) -- saving whatever progress "
                   "was made before re-raising.", type(exc).__name__, exc)

    state_mod.cleanup_old_state_files(keep_today=date_str)
    commit_message = f"update {date_str} @ {now.strftime('%H:%M:%S')}"
    if run_error is not None:
        commit_message += " (partial run -- error occurred, see job log)"
    state_mod.save_state(state, commit_message=commit_message)
    log.info("Run complete for %s @ %s IST.", date_str, now.strftime("%H:%M:%S"))

    if run_error is not None:
        raise run_error


if __name__ == "__main__":
    main()
