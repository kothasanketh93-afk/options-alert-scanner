"""
btst_main.py
------------
Two entry points, run by TWO SEPARATE workflow schedules (see
.github/workflows/btst-entry.yml / btst-exit.yml -- not yet created;
this file is runnable standalone via the CLI args below in the
meantime):

  --entry   Run once, near market close (btst_config.BTST_ENTRY_WINDOW_HHMM).
            Scans BTST_SYMBOLS, opens at most one new overnight
            position per symbol+direction, saves to btst_state.

  --exit    Run repeatedly through the NEXT trading day (same 5-15 min
            cadence as trader.yml is fine). Checks the last
            btst_config.BTST_LOOKBACK_TRADING_DAYS trading days' entry-
            date files for still-open positions (not just yesterday's --
            see run_exit_step()'s docstring for why), advances them
            through btst_tracker.process_position_candles(), sends
            Telegram events, saves back.

Deliberately two thin functions sharing state.py's bootstrap
(ensure_state_branch_checked_out) and auth/config_validation, rather
than one function branching internally -- keeps each path's failure
modes independent (an entry-side bug can't take down exit-day
tracking of already-open real positions, and vice versa).
"""

import sys
import os
import argparse
from datetime import timezone, timedelta

sys.path.insert(0, os.path.dirname(__file__))

import auth
import config_validation
import state as state_mod
import market_data
from exceptions import TelegramFailure, BrokerAPIError
from logging_setup import get_logger

import btst_config
import btst_scanner
import btst_state
import btst_tracker
import btst_telegram
import btst_calendar

IST = timezone(timedelta(hours=5, minutes=30))
BTST_TERMINAL_STATUSES = {"T2_HIT", "SL_HIT", "GAP_SL_HIT", "FORCE_EXIT", "AMBIGUOUS"}
log = get_logger("main")


def new_position_record(setup: dict, position_id: str) -> dict:
    """
    DELIBERATELY does not carry over setup["probability_pct"] /
    ["confidence_score"] / ["expected_rr"] from score_strike()'s
    output, even though they're present on `setup` -- this is NOT an
    oversight, don't "fix" it by adding them back.

    Those numbers are calibrated for the INTRADAY hold: trade_score.
    compute_probability_pct() is a function of trade_score alone, but
    trade_score itself is partly built from risk_reward_strength(),
    which score_strike() computes against the intraday ATR-based SL/
    target distances -- BEFORE btst_scanner.py overwrites entry/SL/
    target with the wider BTST percentage-based levels. So the
    probability number reflects a risk/reward shape this position no
    longer has. trade_score/grade themselves are still carried
    (EMA/VWAP/OI/liquidity/momentum signal quality doesn't change
    just because the hold got longer), only the levels-dependent
    probability/confidence/RR-text derived from them are dropped.

    A real BTST-specific probability model needs its own calibration
    against BTST's actual SL/T1/T2 percentages and, ideally, live
    overnight outcomes -- not a reuse of the intraday number.
    """
    return {
        "position_id": position_id,
        "symbol": setup["symbol"],
        "direction": setup["direction"],
        "instrument_key": setup["instrument_key"],
        "strike": setup["strike"],
        "expiry": setup["expiry"],
        "btst_entry_date": setup["btst_entry_date"],
        "btst_exit_date": setup["btst_exit_date"],
        "alert_premium": setup["alert_premium"],
        "tracked_entry_price": setup["alert_premium"],
        "stop_loss": setup["stop_loss"],
        "target_1": setup["target_1"],
        "target_2": setup["target_2"],
        "trade_score": setup["trade_score"],
        "grade": setup["grade"],
        "status": "OPEN",
        "final_status": None,
        "t1_hit": False,
        "t1_hit_time": None,
        "t2_hit_time": None,
        "sl_hit_time": None,
        "exit_price": None,
        "highest_since_entry": setup["alert_premium"],
        "lowest_since_entry": setup["alert_premium"],
        "max_favorable_pct": 0.0,
        "max_adverse_pct": 0.0,
        # First tracking pass starts from the exit day's market open --
        # market_data.get_1min_candles_since() with this as `since_iso`
        # returns every candle from open onward, same convention
        # tracker.py's WAITING_ENTRY trades use for their first fetch.
        "last_processed_ts": f"{setup['btst_exit_date']}T09:15:00+05:30",
        "messages_sent": [],
    }


def _notify_once(position: dict, event_key: str, message_fn, *args):
    if event_key in position["messages_sent"]:
        return
    text = message_fn(*args)
    btst_telegram.send(text)
    position["messages_sent"].append(event_key)


def run_entry_step():
    now = state_mod.ist_now()
    candidates = btst_scanner.scan_candidates(now)
    if not candidates:
        log.info("BTST_ENTRY: no qualifying candidates this window.")
        return

    entry_date = now.strftime("%Y-%m-%d")
    btst_state_data = btst_state.load(entry_date)

    opened = 0
    seen_symbol_direction = set()
    for setup in candidates:
        key = (setup["symbol"], setup["direction"])
        if key in seen_symbol_direction:
            continue  # keep only the best-scoring strike per symbol+direction, same as main.py's dedup
        seen_symbol_direction.add(key)

        if btst_state.has_open_position(btst_state_data, setup["symbol"], setup["direction"]):
            continue
        if btst_state.open_position_count(btst_state_data) >= btst_config.BTST_MAX_CONCURRENT_POSITIONS:
            break

        position_id = f"BTST-{setup['symbol']}-{setup['direction']}-{entry_date}-{opened + 1:02d}"
        position = new_position_record(setup, position_id)
        btst_state_data["positions"][position_id] = position
        opened += 1

        try:
            _notify_once(position, "NEW_POSITION", btst_telegram.fmt_new_position, position)
        except TelegramFailure:
            log.error("BTST position %s created but the alert could not be sent.", position_id)

    if opened == 0:
        log.info("BTST_ENTRY: candidates found but all were already open or capped -- nothing new.")
        return

    ok = btst_state.save(btst_state_data, commit_message=f"btst entry {entry_date}: +{opened} position(s)")
    if not ok:
        log.error("BTST_ENTRY: failed to save state after opening %d position(s).", opened)


def _dispatch_exit_events(position: dict, events: list):
    for event in events:
        etype = event["type"]
        if etype == "T1_HIT":
            _notify_once(position, "T1_HIT", btst_telegram.fmt_t1_hit, position)
        elif etype == "T2_HIT":
            _notify_once(position, "T2_HIT", btst_telegram.fmt_t2_hit, position)
        elif etype == "SL_HIT":
            _notify_once(position, "SL_HIT", btst_telegram.fmt_sl_hit, position)
        elif etype == "GAP_SL_HIT":
            _notify_once(position, "GAP_SL_HIT", btst_telegram.fmt_gap_sl_hit, position)
        elif etype == "FORCE_EXIT":
            _notify_once(position, "FORCE_EXIT", btst_telegram.fmt_force_exit, position)
        elif etype == "AMBIGUOUS":
            _notify_once(position, "AMBIGUOUS", btst_telegram.fmt_ambiguous, position, event["candle"])


def run_exit_step():
    """
    Checks the last btst_config.BTST_LOOKBACK_TRADING_DAYS trading
    days' entry-date files (not just yesterday's) for any position
    that's still non-terminal and whose exit date has arrived -- so a
    single missed/failed run of this step (a GitHub Actions outage,
    a bad deploy, anything) can't permanently orphan an open overnight
    position. Each entry-date file is loaded and saved independently,
    so one file's failure doesn't block the others.
    """
    now = state_mod.ist_now()
    today_str = now.strftime("%Y-%m-%d")

    entry_dates = []
    cursor = now
    for _ in range(btst_config.BTST_LOOKBACK_TRADING_DAYS):
        cursor = btst_calendar.previous_trading_day(cursor)
        entry_dates.append(cursor.strftime("%Y-%m-%d"))

    any_position_seen = False
    for entry_date in entry_dates:
        btst_state_data = btst_state.load(entry_date)
        positions = btst_state_data["positions"]
        if not positions:
            continue

        touched = False
        for position_id, position in positions.items():
            if position["status"] in BTST_TERMINAL_STATUSES:
                continue
            if position["btst_exit_date"] > today_str:
                continue  # exit day hasn't arrived yet -- shouldn't normally occur, defensive only
            any_position_seen = True

            try:
                candles = market_data.get_1min_candles_since(position["instrument_key"], position["last_processed_ts"])
                if not candles:
                    continue
                events = btst_tracker.process_position_candles(position, candles)
                _dispatch_exit_events(position, events)
                touched = True
            except (BrokerAPIError, TelegramFailure) as exc:
                log.error("BTST position %s (entry %s): exit-step failed (%s: %s) -- will retry next run.",
                          position_id, entry_date, type(exc).__name__, exc)

        if touched:
            ok = btst_state.save(btst_state_data, commit_message=f"btst exit {entry_date} @ {now.strftime('%H:%M:%S')}")
            if not ok:
                log.error("BTST_EXIT: failed to save state for entry date %s.", entry_date)

    if not any_position_seen:
        log.info("BTST_EXIT: no open positions found across the last %d trading day(s).",
                  btst_config.BTST_LOOKBACK_TRADING_DAYS)


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--entry", action="store_true", help="Run the evening entry scan.")
    group.add_argument("--exit", action="store_true", help="Run exit-day tracking.")
    args = parser.parse_args()

    config_validation.validate_startup_config()
    if not auth.trading_enabled_today():
        log.info("SKIP_TRADING_TODAY=true -- skipping BTST run.")
        return

    repo_url = os.environ["REPO_URL"]
    github_token = auth.get_github_token()
    state_mod.ensure_state_branch_checked_out(repo_url, github_token)

    if args.entry:
        run_entry_step()
    else:
        run_exit_step()


if __name__ == "__main__":
    main()
