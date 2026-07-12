"""
main.py
-------
Single entry point run every 5 minutes by the GitHub Actions workflow.
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
import rolling_log
from config import SUMMARY_HHMM

IST = timezone(timedelta(hours=5, minutes=30))
TERMINAL_STATUSES = {"T2_HIT", "SL_HIT", "NOT_ENTERED", "AMBIGUOUS", "EOD_CLOSED"}


def new_trade_record(setup: dict, trade_id: str, alert_dt: datetime) -> dict:
    return {
        "trade_id": trade_id,
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

        # V2.0: grade/confidence/risk replace the raw numeric score --
        # telegram_notify.py must never display setup["_quality_score"].
        "grade": setup["grade"],
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
        "logged_to_rolling_log": False,
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
            tg.send(tg.fmt_circuit_breaker(state_mod.MAX_CONSECUTIVE_SL))
            state["circuit_breaker_notified"] = True
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

    chosen["alternatives"] = [
        {"symbol": c["symbol"], "direction": c["direction"], "moneyness": c["moneyness"], "grade": c["grade"]}
        for c in ranked if c is not chosen
    ]

    trade_id = state_mod.next_trade_id(
        state, chosen["symbol"], chosen["direction"], chosen["moneyness"], now
    )
    trade = new_trade_record(chosen, trade_id, now)
    state["trades"][trade_id] = trade

    notify_once(trade, "NEW_TRADE_ALERT", tg.fmt_new_alert, trade)


def run_tracker_step(state: dict, now: datetime):
    for trade in state["trades"].values():
        if trade["status"] in TERMINAL_STATUSES:
            continue

        candles = market_data.get_1min_candles_since(
            trade["instrument_key"], trade["last_processed_ts"]
        )
        if not candles:
            continue

        events = tracker.process_trade_candles(trade, candles)

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

        if trade["status"] in TERMINAL_STATUSES and not trade["streak_counted"]:
            state_mod.record_trade_outcome_for_streak(state, trade.get("final_status"))
            trade["streak_counted"] = True

        if trade["status"] in TERMINAL_STATUSES and not trade["logged_to_rolling_log"]:
            rolling_log.append_resolved_trade(trade, now)
            trade["logged_to_rolling_log"] = True


def maybe_send_daily_summary(state: dict, now: datetime):
    hhmm = (now.hour, now.minute)
    if hhmm < SUMMARY_HHMM or state.get("summary_sent"):
        return
    trades = list(state["trades"].values())
    if not trades:
        return
    text = tg.fmt_daily_summary(state["date"], trades)
    tg.send(text)
    state["summary_sent"] = True


def main():
    repo_url = os.environ["REPO_URL"]           # e.g. https://github.com/you/repo.git
    github_token = os.environ["GITHUB_TOKEN"]

    now = state_mod.ist_now()
    date_str = state_mod.trading_date_str(now)

    state_mod.ensure_state_branch_checked_out(repo_url, github_token)
    state = state_mod.load_state(date_str)

    run_scanner_step(state, now)
    run_tracker_step(state, now)
    maybe_send_daily_summary(state, now)

    state_mod.cleanup_old_state_files(keep_today=date_str)
    state_mod.save_state(state, commit_message=f"update {date_str} @ {now.strftime('%H:%M:%S')}")


if __name__ == "__main__":
    main()
