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
import logging
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(__file__))

import state as state_mod
import scan_log_store
import tracker
import telegram_notify as tg
import market_data
import scanner
import auth
import config_validation
from exceptions import TelegramFailure, BrokerAPIError
from logging_setup import get_logger
import config
import trade_score
from config import SUMMARY_HHMM
from app.engines import early_breakout as early_breakout_engine
from app.engines import fib_pivot as fib_pivot_engine
from app.market import snapshot as market_snapshot

IST = timezone(timedelta(hours=5, minutes=30))
TERMINAL_STATUSES = {"T2_HIT", "SL_HIT", "NOT_ENTERED", "AMBIGUOUS", "EOD_CLOSED"}
log = get_logger("main")


def new_trade_record(setup: dict, trade_id: str, alert_dt: datetime, display_label: str) -> dict:
    trade = {
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
        "probability_label": setup.get("probability_label"),
        "score_breakdown": setup.get("score_breakdown", []),
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

    # Early Breakout Engine (app/engines/early_breakout/, added 2026-07-24,
    # upgraded to the 5-stage Institutional Accumulation pipeline the same
    # day, migrated from scripts/strategy/ to this package on 2026-07-25
    # as the enterprise-structure pilot -- see app/__init__.py): passthrough
    # fields that only this engine's setups carry. "strategy" defaults to "trade_score" for setups from the
    # existing engine so every trade record can be told apart. The
    # rest are only added when present, so this is a no-op for every
    # existing (non-early-breakout) call site -- inert to tracker.py,
    # state.py, and telegram_notify.fmt_new_alert(), which never read
    # these keys.
    trade["strategy"] = setup.get("strategy", "trade_score")
    for optional_key in (
        "early_breakout_reasons", "base_score", "institutional_score",
        "momentum_score", "smart_money_score", "breakout_price",
        "entry_zone", "maximum_entry",
    ):
        if optional_key in setup:
            trade[optional_key] = setup[optional_key]

    return trade


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

    scanner.reset_shadow_candidates()
    ranked = scanner.detect_all_setups(state["oi_history"])
    # Phase-2 Shadow/Validation Mode (2026-08-23): drains whatever
    # scanner.detect_all_setups() captured on the side this cycle (see
    # scanner.py's own module-level buffer comment) -- completely
    # independent of `ranked`/`chosen` below; a shadow candidate can
    # never influence which live candidate (if any) gets alerted.
    create_shadow_candidates(state, now)

    # Medium priority (EOD daily statistics): fold this run's counts
    # into today's running total. market_data.get_broker().switch_log
    # is THIS RUN's list of primary->secondary broker switches (see
    # broker_failover.py -- switch_log is per-process, not persisted),
    # so its length is this run's broker-error contribution.
    state_mod.record_scan_stats(
        state, scanner.get_last_scan_stats(),
        broker_errors=len(market_data.get_broker().switch_log),
    )

    # Phase 4: fresh probability recalculated THIS scan for every
    # candidate that cleared the gate (not reused from a prior run) --
    # logged into today's history so the evolution and trend
    # (Increasing/Stable/Decreasing) can be shown even for candidates
    # that never became the chosen trade.
    for candidate in ranked:
        state_mod.record_probability(
            state, candidate["symbol"], candidate["direction"],
            candidate["probability_pct"], now,
        )

    chosen = None
    for candidate in ranked:
        if state_mod.has_open_trade(state, candidate["symbol"], candidate["direction"]):
            # This candidate's symbol+direction already has a live trade
            # -- skip it and fall through to the next-best candidate
            # rather than alerting nothing for the whole run just
            # because the single top-ranked pick happened to conflict.
            continue
        if state_mod.cooldown_active(
            state, candidate["symbol"], candidate["direction"], now,
            config.DUPLICATE_ALERT_COOLDOWN_MINUTES,
        ):
            # Same symbol+direction alerted (and possibly already
            # closed) within the configured cooldown window -- skip to
            # avoid re-alerting the same setup back-to-back.
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

    # --- Final Alert Probability Floor (2026-08-10) ---
    # BUGFIX: nothing between candidate selection and trade tracking
    # checked probability at all (see config.MIN_ALERT_PROBABILITY's own
    # comment for the full trace/history). Checked HERE -- before
    # state["trades"][trade_id] = trade below -- so a sub-floor
    # candidate is never tracked either, not just never alerted; this
    # is the single final probability check for this whole pipeline,
    # deliberately placed as the LAST check before a trade record is
    # created, after every hard gate inside score_strike() has already
    # passed (this does not and must not run before those -- it only
    # adds a floor on top of a candidate that already cleared all of
    # them).
    if chosen["probability_pct"] < config.MIN_ALERT_PROBABILITY:
        log.info(
            "PROBABILITY_BELOW_FLOOR %s %s: probability=%s%% < required %s%% -- "
            "no alert, not tracked.",
            chosen["symbol"], chosen["direction"], chosen["probability_pct"],
            config.MIN_ALERT_PROBABILITY,
        )
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

    # Phase 4: today's probability evolution for this symbol+direction
    # (includes the reading just recorded above) plus the derived
    # Increasing/Stable/Decreasing trend, for the Telegram message.
    series = state_mod.probability_series(state, trade["symbol"], trade["direction"])
    trade["probability_history"] = series
    trade["probability_trend"] = trade_score.probability_trend([r["pct"] for r in series])

    state["trades"][trade_id] = trade

    # NOTE (2026-07-24, live bug fix): this used to gate the initial
    # NEW_TRADE_ALERT at probability_pct >= TELEGRAM_MIN_PROBABILITY
    # (80%) while every DOWNSTREAM tracker event for the same trade
    # (ENTRY_ACTIVATED / TARGET_1_HIT / TARGET_2_HIT / SL_HIT, sent
    # unconditionally by run_tracker_step below) was never gated at
    # all. Confirmed live: 2026-07-24's daily summary shows every
    # single trade that day scored 65-79% probability -- entirely
    # below the 80% floor -- so NOT ONE trade got a pre-alert that
    # day, while all six still produced "TARGET 2 HIT -- Trade 6"
    # style messages for a trade the channel was never told existed.
    # That's confusing regardless of probability calibration, so the
    # pre-alert is now sent for every trade that reaches this point
    # (i.e. everything that already cleared ENTRY_SCORE and is about
    # to be tracked) -- consistent with the tracker events it already
    # can't avoid sending. The probability/grade is still printed
    # in the message itself (fmt_new_alert()), so a sub-80% setup is
    # still visibly marked as lower-conviction, just no longer hidden
    # outright. If Telegram volume becomes a problem, prefer raising
    # ENTRY_SCORE (fewer trades tracked at all) over re-adding this
    # gate -- that keeps alerts and outcomes consistent with each
    # other, which silently dropping just the first message did not.
    try:
        notify_once(trade, "NEW_TRADE_ALERT", tg.fmt_new_alert, trade)
    except TelegramFailure:
        # The trade is already recorded in state -- it will still be
        # tracked normally by run_tracker_step. Only the initial alert
        # is missed; log loudly so it's visible in the job log rather
        # than crashing the run and losing tracker-step progress too.
        log.error("New trade %s created but the NEW_TRADE_ALERT could not be "
                   "sent (Telegram failure). Trade will still be tracked.", trade_id)


def _next_early_breakout_trade_id(state: dict, symbol: str, direction: str, alert_dt: datetime) -> str:
    """
    Mirrors state.next_trade_id()'s format but under an "EB-" prefix,
    with its own per-symbol+direction sequence counter kept in the
    SAME state["next_seq"] dict under a different key namespace
    ("EB-{symbol}-{direction}" vs. the main engine's
    "{symbol}-{direction}-{moneyness}"), so the two counters can never
    collide. Not added to state.py itself -- this is scoped to one new
    engine, not a change to the shared trade-ID scheme other code
    depends on.
    """
    key = f"EB-{symbol}-{direction}"
    seq = state["next_seq"].get(key, 0) + 1
    state["next_seq"][key] = seq
    return f"EB-{symbol}-{direction}-{alert_dt.strftime('%Y%m%d')}-{alert_dt.strftime('%H%M')}-{seq:02d}"


def run_early_breakout_step(state: dict, now: datetime):
    """
    Early Breakout Engine (app/engines/early_breakout/, added 2026-07-24,
    migrated from scripts/strategy/ on 2026-07-25 -- see app/__init__.py)
    -- runs every cycle ALONGSIDE run_scanner_step() above, using its own
    scoring (breakout_score.py, its own weights) and its own
    Telegram threshold (EARLY_BREAKOUT_TELEGRAM_MIN_SCORE = 70 out of
    100, vs. the main engine's 80%-probability floor -- the two scores
    aren't on the same scale, so don't compare the raw numbers across
    engines). Shares state.py's has_open_trade()/cooldown_active()/
    concurrent_limit_reached() with the main engine so a symbol+
    direction can't get two live trades from both engines firing on it
    in the same run.

    Unlike run_scanner_step() (which opens only the single best-ranked
    candidate per cycle), this can open one trade per qualifying
    candidate in the same cycle -- Stage 1-3 confirmed setups are rare
    enough events per symbol+direction that treating each as its own
    opportunity (rather than picking only the best across all symbols)
    matches the spec's "every setup that clears the bar gets watched or
    alerted" framing. concurrent_limit_reached() still caps the total.
    """
    if state_mod.circuit_breaker_active(state):
        return  # same circuit breaker as run_scanner_step(); already notified there

    candidates = early_breakout_engine.detect_all_early_breakout_setups(state["oi_history"])
    if not candidates:
        return

    for candidate in candidates:
        if state_mod.has_open_trade(state, candidate["symbol"], candidate["direction"]):
            continue
        if state_mod.cooldown_active(
            state, candidate["symbol"], candidate["direction"], now,
            config.DUPLICATE_ALERT_COOLDOWN_MINUTES,
        ):
            continue
        if state_mod.concurrent_limit_reached(state):
            return  # cap hit -- stop opening more this cycle, same as run_scanner_step()

        trade_id = _next_early_breakout_trade_id(state, candidate["symbol"], candidate["direction"], now)
        display_label = f"Trade {state_mod.next_display_number(state)}"
        trade = new_trade_record(candidate, trade_id, now, display_label)
        state["trades"][trade_id] = trade

        # Fix 12: 60-69 Watchlist only (tracked, no Telegram); 70+ alert.
        if trade["trade_score"] < config.EARLY_BREAKOUT_TELEGRAM_MIN_SCORE:
            log.info(
                "EB_WATCHLIST %s: score %d/100 (%s) is below the %d Telegram floor -- "
                "tracked internally, no alert sent.",
                trade_id, trade["trade_score"], trade.get("grade"),
                config.EARLY_BREAKOUT_TELEGRAM_MIN_SCORE,
            )
            continue

        try:
            notify_once(trade, "NEW_TRADE_ALERT", tg.fmt_early_breakout_alert, trade)
        except TelegramFailure:
            log.error("Early breakout trade %s created but the alert could not be "
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
            # Centralized outcome tracking (2026-08-14, architecture
            # cleanup audit) -- `mins` used to be computed here ONLY as
            # a formatting argument for the Telegram message, never
            # stored back onto `trade` -- meaning it was lost the
            # moment the message was sent, unavailable to
            # save_state()/scan_log_store's persisted trade record for
            # any future calibration dataset. max_favorable_pct/
            # max_adverse_pct (this file's own MFE/MAE) were already
            # correctly persisted this way -- this brings
            # time-to-target/time-to-stop to the same standard.
            trade["time_to_target_1_minutes"] = mins
            notify_once(trade, "T1_HIT", tg.fmt_t1_hit, trade, mins)
        elif etype == "T2_HIT":
            mins = tracker.compute_time_taken(trade["actual_entry_time"], trade["t2_hit_time"])
            trade["time_to_target_2_minutes"] = mins
            notify_once(trade, "T2_HIT", tg.fmt_t2_hit, trade, mins)
        elif etype == "SL_HIT":
            mins = tracker.compute_time_taken(trade["actual_entry_time"], trade["sl_hit_time"])
            trade["time_to_stop_minutes"] = mins
            notify_once(trade, "SL_HIT", tg.fmt_sl_hit, trade, mins)
        elif etype == "TRAILING_SL_ADJUSTED":
            # 2026-07-31: notify_once keys on trade["messages_sent"], which
            # already guarantees this fires at most once per trade -- matches
            # _maybe_apply_trailing_stop()'s own "applies once" invariant, so
            # no extra dedup logic needed here.
            notify_once(trade, "TRAILING_SL_ADJUSTED", tg.fmt_trailing_sl_adjusted, trade)
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
                final_status = trade.get("final_status")
                state_mod.record_trade_outcome_for_streak(state, final_status)
                trade["streak_counted"] = True
                if final_status in ("SL_HIT", "T1_THEN_SL") and not config.STOP_AFTER_CONSECUTIVE_SL:
                    log.info(
                        "Trade closed with Stop Loss. Scanner continues because "
                        "stop_after_consecutive_sl is disabled."
                    )

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


def _shadow_entry_context(trade: dict) -> str:
    """
    Diagnostic-only (added 2026-10-31): extra key=value fields appended to
    SHADOW_ENTRY_ACTIVATED / SHADOW_OUTCOME_SL_FIRST log lines so the
    retained scan logs carry what is needed to study "chased entry" SLs
    (entry premium vs. alert premium, alert->entry delay, grade, score,
    session, MFE/MAE) without needing the state file. Never changes any
    trade field or decision; any missing/odd value just omits that field.
    """
    parts = []
    try:
        ap, ep = trade.get("alert_premium"), trade.get("tracked_entry_price")
        if ap and ep:
            parts.append(f"alert_premium={ap}")
            parts.append(f"entry_price={ep}")
            parts.append(f"chase_pct={round((ep - ap) / ap * 100, 2)}")
        at, et = trade.get("alert_time"), trade.get("actual_entry_time")
        if at and et:
            delay = (datetime.fromisoformat(et) - datetime.fromisoformat(at)).total_seconds() / 60
            parts.append(f"alert_to_entry_min={round(delay, 1)}")
        for key, label in (("grade", "grade"), ("trade_score", "score"), ("session", "session"),
                           ("max_favorable_pct", "mfe_pct"), ("max_adverse_pct", "mae_pct")):
            if trade.get(key) is not None:
                parts.append(f"{label}={str(trade[key]).replace(' ', '_')}")
    except Exception:
        return ""
    return (" " + " ".join(parts)) if parts else ""


def _dispatch_shadow_tracker_events(trade: dict, events: list, now: datetime):
    """
    Phase-2 Shadow/Validation Mode (2026-08-23) counterpart to
    _dispatch_tracker_events() above -- SAME event types from the SAME
    tracker.process_trade_candles() call, but LOGS each outcome instead
    of ever calling notify_once()/Telegram. This is the one place that
    structurally guarantees requirement #8 (shadow candidates cannot be
    accidentally converted into live alerts) -- there is no Telegram
    call anywhere in this function to accidentally trigger, unlike
    _dispatch_tracker_events() which exists specifically to send them.
    """
    for event in events:
        etype = event["type"]
        if etype == "ENTRY_ACTIVATED":
            log.info("SHADOW_ENTRY_ACTIVATED %s %s %s strike=%s candidate_id=%s%s",
                     trade["symbol"], trade["direction"], trade["moneyness"],
                     trade["strike"], trade.get("candidate_id"),
                     _shadow_entry_context(trade))
        elif etype == "T1_HIT":
            mins = tracker.compute_time_taken(trade["actual_entry_time"], trade["t1_hit_time"])
            trade["time_to_target_1_minutes"] = mins
            log.info("SHADOW_OUTCOME_T1_FIRST %s %s strike=%s candidate_id=%s time_to_t1_minutes=%s",
                     trade["symbol"], trade["direction"], trade["strike"], trade.get("candidate_id"), mins)
        elif etype == "T2_HIT":
            mins = tracker.compute_time_taken(trade["actual_entry_time"], trade["t2_hit_time"])
            trade["time_to_target_2_minutes"] = mins
            log.info("SHADOW_OUTCOME_T2_FIRST %s %s strike=%s candidate_id=%s time_to_t2_minutes=%s",
                     trade["symbol"], trade["direction"], trade["strike"], trade.get("candidate_id"), mins)
        elif etype == "SL_HIT":
            mins = tracker.compute_time_taken(trade["actual_entry_time"], trade["sl_hit_time"])
            trade["time_to_stop_minutes"] = mins
            log.info("SHADOW_OUTCOME_SL_FIRST %s %s strike=%s candidate_id=%s time_to_sl_minutes=%s%s",
                     trade["symbol"], trade["direction"], trade["strike"], trade.get("candidate_id"), mins,
                     _shadow_entry_context(trade))
        elif etype == "NOT_ENTERED":
            log.info("SHADOW_OUTCOME_NOT_ENTERED %s %s strike=%s candidate_id=%s",
                     trade["symbol"], trade["direction"], trade["strike"], trade.get("candidate_id"))
        elif etype == "EOD_CLOSED":
            # Shadow TIMEOUT Extension (2026-09-19, config.
            # SHADOW_TIMEOUT_EXTENSION_ENABLED). With the flag off
            # (default False), this is unchanged: log TIMEOUT, the
            # EOD_CLOSED status set by tracker.py stays terminal, this
            # candidate is never picked up again. With the flag on,
            # extend rather than finalize -- UNLESS the contract's own
            # expiry has been reached, or the extension budget
            # (SHADOW_TIMEOUT_EXTENSION_MAX_DAYS additional trading
            # days beyond entry day) is already used up -- in either
            # of those cases, fall through to the same final TIMEOUT
            # logging as the flag-off path, so a candidate can never be
            # extended past what config allows or past its own expiry.
            days_used = trade.get("timeout_extension_days_used", 0)
            expiry_reached = now.strftime("%Y-%m-%d") >= trade["expiry"]
            if (config.SHADOW_TIMEOUT_EXTENSION_ENABLED and not expiry_reached
                    and days_used < config.SHADOW_TIMEOUT_EXTENSION_MAX_DAYS):
                # Reset back to the SAME state tracker.py's own
                # ACTIVE -> EOD_CLOSED transition moved it FROM (both
                # branches that set EOD_CLOSED in tracker.py transition
                # from ACTIVE -- see that file's own state-machine
                # comment) -- next run's market_data.get_1min_candles_
                # since(instrument_key, last_processed_ts) naturally
                # picks up the NEXT trading day's candles once the
                # market reopens, since last_processed_ts is left
                # exactly where tracker.py set it (the EOD candle just
                # processed). Entry/SL/T1/T2 are deliberately NOT
                # recalculated -- see config.SHADOW_TIMEOUT_EXTENSION_
                # ENABLED's own comment for why keeping the SAME levels
                # across the extension is the correct choice here, not
                # a temporary simplification.
                trade["status"] = "ACTIVE"
                trade["final_status"] = None
                trade["timeout_extension_days_used"] = days_used + 1
                log.info(
                    "SHADOW_TIMEOUT_EXTENDED %s %s strike=%s candidate_id=%s "
                    "extension_day=%d/%d",
                    trade["symbol"], trade["direction"], trade["strike"],
                    trade.get("candidate_id"), days_used + 1, config.SHADOW_TIMEOUT_EXTENSION_MAX_DAYS,
                )
            else:
                if trade.get("timeout_extension_days_used"):
                    log.info(
                        "SHADOW_OUTCOME_TIMEOUT %s %s strike=%s candidate_id=%s "
                        "(after %d extension day(s)%s)",
                        trade["symbol"], trade["direction"], trade["strike"], trade.get("candidate_id"),
                        trade["timeout_extension_days_used"],
                        ", expiry reached" if expiry_reached else "",
                    )
                else:
                    log.info("SHADOW_OUTCOME_TIMEOUT %s %s strike=%s candidate_id=%s",
                             trade["symbol"], trade["direction"], trade["strike"], trade.get("candidate_id"))
        elif etype in ("AMBIGUOUS", "AMBIGUOUS_AFTER_T1"):
            log.info("SHADOW_OUTCOME_AMBIGUOUS %s %s strike=%s candidate_id=%s",
                     trade["symbol"], trade["direction"], trade["strike"], trade.get("candidate_id"))
        # TRAILING_SL_ADJUSTED: intentionally not logged as a distinct
        # SHADOW_OUTCOME_* line -- it isn't a terminal outcome, and
        # trailing-stop mechanics are already covered by the live path;
        # process_trade_candles() still updates trade["stop_loss"] in
        # place regardless of whether this function logs it.


def create_shadow_candidates(state: dict, now: datetime):
    """
    Phase-2 Shadow/Validation Mode (2026-08-23). Drains
    scanner.get_shadow_candidates() (populated during THIS cycle's
    scanner.detect_setups() calls, one entry per candidate that
    cleared every hard gate but had no calibrated probability) and
    creates a shadow trade record for each -- reusing main.
    new_trade_record() UNCHANGED (same trade-record shape the live
    path uses, so tracker.process_trade_candles() and
    tracker.process_trade_candles() works on shadow trades with zero
    code duplication) plus the shadow-specific tag
    fields already present on the candidate dict itself
    (probability_status/execution_mode/live_alert_sent/candidate_id).

    No-op entirely (does not even drain the buffer) unless
    config.PROBABILITY_SHADOW_MODE_ENABLED is True.

    Dedup (requirement #7): a candidate_id already present among
    state["shadow_trades"] in a NON-terminal status is not recreated --
    the same setup re-appearing on a later 5-minute cycle (the SAME
    "is this one evolving candidate or four independent ones" question
    this session's own scan_log_store.py audit raised for the
    troubleshooting log) must not spawn duplicate shadow trades. A
    candidate_id whose prior shadow trade already resolved to a
    terminal status IS allowed to create a fresh one -- that's a
    genuinely new occurrence of the same setup shape on a later day/
    session, not a duplicate of the one already tracked to completion.
    """
    if not config.PROBABILITY_SHADOW_MODE_ENABLED:
        return

    shadow_candidates = scanner.get_shadow_candidates()
    if not shadow_candidates:
        return

    shadow_trades = state.setdefault("shadow_trades", {})
    active_candidate_ids = {
        t.get("candidate_id") for t in shadow_trades.values()
        if t["status"] not in TERMINAL_STATUSES
    }

    for candidate in shadow_candidates:
        candidate_id = candidate["candidate_id"]
        if candidate_id in active_candidate_ids:
            log.info("SHADOW_CANDIDATE_DUPLICATE_SKIPPED candidate_id=%s -- already tracked, not terminal", candidate_id)
            continue

        shadow_id = f"SHADOW-{len(shadow_trades) + 1}-{now.strftime('%H%M%S')}"
        display_label = f"Shadow {len(shadow_trades) + 1}"
        trade = new_trade_record(candidate, shadow_id, now, display_label)
        # Shadow-specific tags, on top of the otherwise-identical live
        # trade-record shape -- these are what every consumer (this
        # file's own run_shadow_tracker_step(), any future export/
        # analysis tooling) checks to confirm this is a shadow record,
        # never a live one.
        trade["probability_status"] = candidate["probability_status"]
        trade["execution_mode"] = candidate["execution_mode"]
        trade["live_alert_sent"] = False
        trade["candidate_id"] = candidate_id

        shadow_trades[shadow_id] = trade
        active_candidate_ids.add(candidate_id)
        log.info(
            "SHADOW_CANDIDATE_CREATED shadow_id=%s %s %s strike=%s candidate_id=%s trade_score=%s/100",
            shadow_id, trade["symbol"], trade["direction"], trade["strike"], candidate_id, trade["trade_score"],
        )


def run_shadow_tracker_step(state: dict, now: datetime):
    """
    Phase-2 Shadow/Validation Mode (2026-08-23) counterpart to
    run_tracker_step() above -- SAME candle-fetch/process_trade_candles()
    mechanism, applied to state["shadow_trades"] instead of
    state["trades"], dispatched via _dispatch_shadow_tracker_events()
    (log-only, never Telegram) instead of _dispatch_tracker_events().

    No-op entirely unless config.PROBABILITY_SHADOW_MODE_ENABLED is
    True -- with the flag off, state.get("shadow_trades", {}) is always
    empty anyway (create_shadow_candidates() never populated it), so
    this loop would do nothing regardless; the explicit early return
    just avoids the no-op iteration/log-noise cost every cycle.
    """
    if not config.PROBABILITY_SHADOW_MODE_ENABLED:
        return

    for shadow_id, trade in state.get("shadow_trades", {}).items():
        if trade["status"] in TERMINAL_STATUSES:
            continue
        try:
            candles = market_data.get_1min_candles_since(
                trade["instrument_key"], trade["last_processed_ts"]
            )
            if not candles:
                continue
            events = tracker.process_trade_candles(trade, candles)
            _dispatch_shadow_tracker_events(trade, events, now)
        except (BrokerAPIError, TelegramFailure) as exc:
            # Same per-trade containment posture as run_tracker_step()
            # above -- one shadow trade's broker hiccup must not stop
            # every other shadow (or live) trade's tracking this cycle.
            # TelegramFailure is caught here defensively even though
            # this path never calls Telegram -- purely to keep this
            # function's error-handling contract identical to its live
            # counterpart, in case a future change to
            # _dispatch_shadow_tracker_events() ever needed one.
            log.error("Shadow trade %s: tracker step failed (%s: %s) -- continuing with "
                      "remaining shadow trades; this one will be retried next run.",
                      shadow_id, type(exc).__name__, exc)


def maybe_send_daily_summary(state: dict, now: datetime):
    hhmm = (now.hour, now.minute)
    if hhmm < SUMMARY_HHMM or state.get("summary_sent"):
        return
    trades = list(state["trades"].values())
    if not trades:
        return
    text = tg.fmt_daily_summary(
        state["date"], trades,
        daily_stats=state.get("daily_stats"),
        probability_stats=state_mod.probability_stats(state),
    )
    try:
        tg.send(text)
        state["summary_sent"] = True
    except TelegramFailure:
        # Leave summary_sent False so it's retried next run instead of
        # silently never being sent for the day.
        log.error("Daily summary could not be sent (Telegram failure) -- will retry next run.")


def maybe_send_shadow_summary(state: dict, now: datetime):
    """
    Phase-2 Shadow/Validation Mode (2026-08-23) daily dataset summary,
    per requirement #10 (part 2): total_shadow_candidates, T1_FIRST,
    SL_FIRST, T2_FIRST, TIMEOUT, INVALIDATED, AMBIGUOUS. A SEPARATE
    Telegram message from maybe_send_daily_summary() above -- that
    function returns early on `if not trades: return`, i.e. it never
    sends at all while zero LIVE trades exist (today's actual reality,
    per calibrated_probability_pct() always returning None), so a
    shadow-only summary would never surface if folded into it. Sent
    independently, gated on shadow_trades existing (not live trades),
    idempotent via its own state["shadow_summary_sent"] flag (same
    pattern as summary_sent above, deliberately a SEPARATE flag so
    neither summary's send/retry status affects the other's).

    No-op entirely unless config.PROBABILITY_SHADOW_MODE_ENABLED is
    True.

    final_status -> bucket mapping (tracker.py's own vocabulary is
    finer-grained than the 6 buckets requested; documented here so a
    future reader can see exactly which raw statuses fold into which
    bucket):
      T2_FIRST:    T2_HIT
      SL_FIRST:    SL_HIT, TRAILING_SL_HIT (no T1 first)
      T1_FIRST:    T1_THEN_SL, T1_THEN_TRAILING_SL, T1_HIT_THEN_EOD
                   (T1 reached, did not go on to T2)
      AMBIGUOUS:   AMBIGUOUS, T1_HIT_THEN_AMBIGUOUS
      TIMEOUT:     EOD_CLOSED (never reached T1 at all, closed at EOD)
      INVALIDATED: NOT_ENTERED (never got a valid entry fill)
    """
    if not config.PROBABILITY_SHADOW_MODE_ENABLED:
        return
    hhmm = (now.hour, now.minute)
    if hhmm < SUMMARY_HHMM or state.get("shadow_summary_sent"):
        return
    shadow_trades = list(state.get("shadow_trades", {}).values())
    if not shadow_trades:
        return

    _BUCKET_MAP = {
        "T2_HIT": "T2_FIRST",
        "SL_HIT": "SL_FIRST", "TRAILING_SL_HIT": "SL_FIRST",
        "T1_THEN_SL": "T1_FIRST", "T1_THEN_TRAILING_SL": "T1_FIRST", "T1_HIT_THEN_EOD": "T1_FIRST",
        "AMBIGUOUS": "AMBIGUOUS", "T1_HIT_THEN_AMBIGUOUS": "AMBIGUOUS",
        "EOD_CLOSED": "TIMEOUT",
        "NOT_ENTERED": "INVALIDATED",
    }
    counts = {"T1_FIRST": 0, "SL_FIRST": 0, "T2_FIRST": 0, "TIMEOUT": 0, "INVALIDATED": 0, "AMBIGUOUS": 0}
    still_active = 0
    for trade in shadow_trades:
        final_status = trade.get("final_status")
        bucket = _BUCKET_MAP.get(final_status)
        if bucket:
            counts[bucket] += 1
        elif trade["status"] not in TERMINAL_STATUSES:
            still_active += 1

    lines = [
        f"📊 Shadow/Validation Dataset Summary -- {state['date']}",
        f"total_shadow_candidates: {len(shadow_trades)}",
        f"still_active: {still_active}",
    ] + [f"{bucket}: {count}" for bucket, count in counts.items()]
    text = "\n".join(lines)
    try:
        tg.send(text)
        state["shadow_summary_sent"] = True
    except TelegramFailure:
        log.error("Shadow summary could not be sent (Telegram failure) -- will retry next run.")


def main():
    config_validation.validate_startup_config()

    # (2026-10-31: the per-run News Intelligence collection cycle that used
    # to run here was removed -- its only consumer, decision_engine's
    # news_adapter, had not been called in the live path since 2026-08-14.)

    # Part 9 item 5: read-only pass over config.py's actual VALUES
    # (duplicate symbols, negative intervals, invalid thresholds/timings)
    # -- distinct from validate_startup_config() above, which only checks
    # required env vars/secrets are present. Never raises: logs findings
    # and continues, so this cannot change this run's outcome either way.
    try:
        from production.config_validator import validate_config
        cfg_report = validate_config()
        if not cfg_report.is_valid:
            log.error("config validation found error(s):\n%s", cfg_report.summary())
        elif cfg_report.warnings:
            log.warning("config validation found warning(s):\n%s", cfg_report.summary())
    except Exception as exc:  # noqa: BLE001 - a validator bug must never block a scan run
        log.warning("config validation could not complete: %s", exc)

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

    # Centralized troubleshooting log retention (2026-08-12): captures
    # every "trading_system.*" log line emitted during this run (same
    # root logger logging_setup.py's own stdout handler is attached to
    # -- this is a SECOND, independent handler, nothing about the
    # existing stdout logging changes) for persistence to the `state`
    # git branch. See scan_log_store.py's module docstring for the full
    # design. Attached here (state branch clone dir is ready) and
    # flushed/detached near the end of this function, alongside the
    # existing state save -- deliberately outside the try/except below,
    # same reasoning as state_mod.save_state(): a run that errors out
    # partway through should still persist whatever it already logged,
    # not discard it.
    scan_cycle_id = f"{date_str}-{now.strftime('%H%M%S')}"
    _scan_log_handler = scan_log_store.ScanLogCaptureHandler(scan_cycle_id)
    logging.getLogger("trading_system").addHandler(_scan_log_handler)

    # Market context, logging-only (2026-08-14, architecture cleanup) --
    # see market_data.get_india_vix()'s own comment: never wired into
    # scoring/gating, fetched once per run (not once per strike),
    # purely so a human reading the scan-log store has today's
    # volatility-regime context alongside the gate-by-gate evidence.
    india_vix = market_data.get_india_vix()
    log.info("MARKET_CONTEXT_VIX %s", india_vix if india_vix is not None else "UNAVAILABLE")

    # Phase 1 (opportunity_score.py): swap SYMBOLS/STOCK_SYMBOLS from
    # the safe import-time fallback (config.FALLBACK_STOCK_WATCHLIST)
    # to today's real opportunity shortlist, now that the state branch
    # is actually checked out and daily_cache.py has something to
    # read. MUST happen before run_scanner_step() below -- see
    # config.refresh_symbols_for_today()'s docstring for why this
    # can't just happen at `import config` time.
    config.refresh_symbols_for_today()

    state = state_mod.load_state(date_str)

    # Phase 6 (intraday_rerank.py): if this run's clock time lands in one
    # of config.RERANK_TIMES's windows, re-score the universe and swap
    # config.STOCK_SYMBOLS/SYMBOLS in place BEFORE run_scanner_step()
    # below -- so this same run's option-chain scan already uses the
    # freshly re-ranked SCAN_UNIVERSE. Best-effort: any failure here
    # leaves SCAN_UNIVERSE exactly as config.refresh_symbols_for_today()
    # just set it, never blocks the rest of the run.
    try:
        import intraday_rerank
        intraday_rerank.maybe_rerank(state, now)
    except Exception as exc:
        log.error("Intraday rerank step failed (%s: %s) -- continuing with "
                   "the current SCAN_UNIVERSE.", type(exc).__name__, exc, exc_info=True)

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
        try:
            # Isolated in its own try/except: a bug in the newer Early
            # Breakout Engine should never prevent the existing
            # engine's tracker step or daily summary from running --
            # same "one bad symbol shouldn't sink the run" principle
            # detect_all_setups()'s per-symbol try/except already
            # applies inside each engine.
            run_early_breakout_step(state, now)
        except Exception as exc:
            log.error("Early Breakout Engine step failed (%s: %s) -- continuing with "
                       "the rest of this run.", type(exc).__name__, exc, exc_info=True)
        try:
            create_fib_pivot_shadow_candidates(state, now)
        except Exception as exc:
            log.error("Fib Pivot Shadow Engine step failed (%s: %s) -- "
                       "continuing with the rest of this run.",
                       type(exc).__name__, exc, exc_info=True)
        run_tracker_step(state, now)
        run_shadow_tracker_step(state, now)
        run_fib_pivot_shadow_step(state, now)

        maybe_send_daily_summary(state, now)
        # BUGFIX (2026-09-10, live-log finding): maybe_send_shadow_summary()
        # was DEFINED (see its own docstring) but never actually CALLED
        # anywhere in main() -- confirmed live: with
        # PROBABILITY_SHADOW_MODE_ENABLED=True and shadow_trades genuinely
        # non-empty (80 candidates over 4 days), the "Shadow/Validation
        # Dataset Summary" Telegram message never appeared even after
        # SUMMARY_HHMM's own separate timing bug was fixed on 2026-09-01
        # -- because the timing fix was moot without this call existing
        # at all. Placed right after maybe_send_daily_summary() so both
        # summaries are attempted at the same point in the run, same as
        # they were always intended to be.
        maybe_send_shadow_summary(state, now)
    except Exception as exc:
        run_error = exc
        log.error("Unexpected error during run (%s: %s) -- saving whatever progress "
                   "was made before re-raising.", type(exc).__name__, exc, exc_info=True)

    state_mod.cleanup_old_state_files(keep_today=date_str)
    commit_message = f"update {date_str} @ {now.strftime('%H:%M:%S')}"
    if run_error is not None:
        commit_message += " (partial run -- error occurred, see job log)"
    state_mod.save_state(state, commit_message=commit_message)

    logging.getLogger("trading_system").removeHandler(_scan_log_handler)
    scan_log_store.run_retention_and_flush(_scan_log_handler.records, date_str)

    log.info("Run complete for %s @ %s IST.", date_str, now.strftime("%H:%M:%S"))

    if run_error is not None:
        raise run_error



def _next_fib_pivot_trade_id(state: dict, symbol: str, direction: str, alert_dt: datetime) -> str:
    """
    Fibonacci Pivot Shadow Engine (app/engines/fib_pivot/, added
    2026-10-30) -- mirrors _next_early_breakout_trade_id()'s pattern
    exactly: own "FIBPIVOT-" prefix, own per-symbol+direction sequence
    counter in the SAME state["next_seq"] dict under a key namespace
    ("FIBPIVOT-{symbol}-{direction}") that can never collide with the
    main engine's or the Early Breakout Engine's own keys.
    """
    key = f"FIBPIVOT-{symbol}-{direction}"
    seq = state["next_seq"].get(key, 0) + 1
    state["next_seq"][key] = seq
    return f"FIBPIVOT-{symbol}-{direction}-{alert_dt.strftime('%Y%m%d')}-{alert_dt.strftime('%H%M')}-{seq:02d}"


def _dispatch_fib_pivot_shadow_events(trade: dict, events: list):
    """
    Fibonacci Pivot Shadow Engine counterpart to
    _dispatch_shadow_tracker_events() -- SAME event types from the SAME
    tracker.process_trade_candles() call, but every log line uses the
    "FIBPIVOT_SHADOW_" prefix instead of "SHADOW_", so this strategy's
    forward-collected data can NEVER be accidentally mixed into the
    already-analyzed main-engine shadow dataset (N=139, this session's
    own analysis) by a future log-scraping script that greps for
    "SHADOW_OUTCOME_". There is no Telegram call anywhere in this
    function, by design -- same structural guarantee
    _dispatch_shadow_tracker_events() relies on.

    Deliberately simpler than _dispatch_shadow_tracker_events(): no
    SHADOW_TIMEOUT_EXTENSION_ENABLED-style multi-day extension here yet.
    An EOD_CLOSED candle just logs a final TIMEOUT outcome. This is a
    conscious scope cut, not an oversight -- see this function's own
    "known simplification" note below -- and can be added later once
    there's a reason to believe the extra complexity is worth it for
    THIS strategy specifically.
    """
    for event in events:
        etype = event["type"]
        if etype == "ENTRY_ACTIVATED":
            log.info("FIBPIVOT_SHADOW_ENTRY_ACTIVATED %s %s strike=%s candidate_id=%s",
                     trade["symbol"], trade["direction"], trade["strike"], trade.get("candidate_id"))
        elif etype == "T1_HIT":
            mins = tracker.compute_time_taken(trade["actual_entry_time"], trade["t1_hit_time"])
            trade["time_to_target_1_minutes"] = mins
            log.info("FIBPIVOT_SHADOW_OUTCOME_T1_FIRST %s %s strike=%s candidate_id=%s time_to_t1_minutes=%s",
                     trade["symbol"], trade["direction"], trade["strike"], trade.get("candidate_id"), mins)
        elif etype == "T2_HIT":
            mins = tracker.compute_time_taken(trade["actual_entry_time"], trade["t2_hit_time"])
            trade["time_to_target_2_minutes"] = mins
            log.info("FIBPIVOT_SHADOW_OUTCOME_T2_FIRST %s %s strike=%s candidate_id=%s time_to_t2_minutes=%s",
                     trade["symbol"], trade["direction"], trade["strike"], trade.get("candidate_id"), mins)
        elif etype == "SL_HIT":
            mins = tracker.compute_time_taken(trade["actual_entry_time"], trade["sl_hit_time"])
            trade["time_to_stop_minutes"] = mins
            log.info("FIBPIVOT_SHADOW_OUTCOME_SL_FIRST %s %s strike=%s candidate_id=%s time_to_sl_minutes=%s",
                     trade["symbol"], trade["direction"], trade["strike"], trade.get("candidate_id"), mins)
        elif etype == "NOT_ENTERED":
            log.info("FIBPIVOT_SHADOW_OUTCOME_NOT_ENTERED %s %s strike=%s candidate_id=%s",
                     trade["symbol"], trade["direction"], trade["strike"], trade.get("candidate_id"))
        elif etype == "EOD_CLOSED":
            # Known simplification (see docstring above): no multi-day
            # extension yet -- an EOD_CLOSED candle finalizes as TIMEOUT
            # every time.
            log.info("FIBPIVOT_SHADOW_OUTCOME_TIMEOUT %s %s strike=%s candidate_id=%s",
                     trade["symbol"], trade["direction"], trade["strike"], trade.get("candidate_id"))
        elif etype in ("AMBIGUOUS", "AMBIGUOUS_AFTER_T1"):
            log.info("FIBPIVOT_SHADOW_OUTCOME_AMBIGUOUS %s %s strike=%s candidate_id=%s",
                     trade["symbol"], trade["direction"], trade["strike"], trade.get("candidate_id"))
        # TRAILING_SL_ADJUSTED: not logged as a distinct outcome line, same
        # reasoning as _dispatch_shadow_tracker_events()'s own comment --
        # it isn't terminal, and tracker.py already updates trade["stop_loss"]
        # in place regardless of whether this function logs it.


def create_fib_pivot_shadow_candidates(state: dict, now: datetime):
    """
    Fibonacci Pivot Shadow Engine (app/engines/fib_pivot/, added
    2026-10-30). Mirrors create_shadow_candidates()'s structure, but:

      - reads from fib_pivot_engine.detect_all_fib_pivot_setups()
        instead of scanner.get_shadow_candidates() -- a completely
        separate detection pipeline (its own EMA9 + Fibonacci-pivot +
        close-confirmation logic, see that package's engine.py)
      - stores results in state["fib_pivot_shadow_trades"], NEVER
        state["shadow_trades"] or state["trades"] -- this is the single
        line that keeps this strategy's forward data fully isolated
        from both the live-alert path and the already-validated
        Phase-2 shadow dataset.
      - uses display labels that do NOT consume
        state_mod.next_display_number() -- that counter numbers
        Telegram-facing "Trade N" labels for the live engine and Early
        Breakout Engine; this engine never sends Telegram, so it must
        not perturb that shared sequence.

    No-op entirely unless config.FIB_PIVOT_SHADOW_MODE_ENABLED is True.
    Same dedup rule as create_shadow_candidates(): a candidate_id
    already open (non-terminal) in fib_pivot_shadow_trades is not
    recreated; one that already resolved to a terminal status IS
    allowed to fire again later (a genuinely new occurrence).
    """
    if not config.FIB_PIVOT_SHADOW_MODE_ENABLED:
        return

    try:
        candidates = fib_pivot_engine.detect_all_fib_pivot_setups()
    except Exception as exc:
        # Same isolation posture as run_early_breakout_step()'s own
        # try/except in main() below -- a bug in this brand-new, as-yet
        # unvalidated engine must never affect the live engine, the
        # Early Breakout Engine, or the existing shadow dataset.
        log.error("Fib Pivot Shadow Engine: detect_all_fib_pivot_setups() failed (%s: %s) -- "
                   "skipping this cycle.", type(exc).__name__, exc, exc_info=True)
        return

    if not candidates:
        return

    fib_pivot_shadow_trades = state.setdefault("fib_pivot_shadow_trades", {})
    active_candidate_ids = {
        t.get("candidate_id") for t in fib_pivot_shadow_trades.values()
        if t["status"] not in TERMINAL_STATUSES
    }

    for candidate in candidates:
        candidate_id = f"{candidate['symbol']}:{candidate['direction']}:{candidate['strike']}:{candidate['expiry']}"
        if candidate_id in active_candidate_ids:
            log.info("FIBPIVOT_SHADOW_CANDIDATE_DUPLICATE_SKIPPED candidate_id=%s -- already tracked, not terminal",
                      candidate_id)
            continue

        trade_id = _next_fib_pivot_trade_id(state, candidate["symbol"], candidate["direction"], now)
        display_label = f"FibPivotShadow {len(fib_pivot_shadow_trades) + 1}"
        trade = new_trade_record(candidate, trade_id, now, display_label)
        trade["probability_status"] = "shadow_only"
        trade["execution_mode"] = "shadow"
        trade["live_alert_sent"] = False
        trade["candidate_id"] = candidate_id
        trade["engine"] = "fib_pivot"

        fib_pivot_shadow_trades[trade_id] = trade
        active_candidate_ids.add(candidate_id)
        log.info(
            "FIBPIVOT_SHADOW_CANDIDATE_CREATED trade_id=%s %s %s strike=%s candidate_id=%s "
            "pivot_level=%s trade_score=%s/100",
            trade_id, trade["symbol"], trade["direction"], trade["strike"], candidate_id,
            candidate.get("pivot_level_name"), trade["trade_score"],
        )


def run_fib_pivot_shadow_step(state: dict, now: datetime):
    """
    Fibonacci Pivot Shadow Engine counterpart to run_shadow_tracker_step()
    -- same candle-fetch/process_trade_candles() mechanism, applied to
    state["fib_pivot_shadow_trades"], dispatched via
    _dispatch_fib_pivot_shadow_events() (log-only, never Telegram).

    No-op entirely unless config.FIB_PIVOT_SHADOW_MODE_ENABLED is True.
    """
    if not config.FIB_PIVOT_SHADOW_MODE_ENABLED:
        return

    for trade_id, trade in state.get("fib_pivot_shadow_trades", {}).items():
        if trade["status"] in TERMINAL_STATUSES:
            continue
        try:
            candles = market_data.get_1min_candles_since(
                trade["instrument_key"], trade["last_processed_ts"]
            )
            if not candles:
                continue
            events = tracker.process_trade_candles(trade, candles)
            _dispatch_fib_pivot_shadow_events(trade, events)
        except (BrokerAPIError, TelegramFailure) as exc:
            # Same per-trade containment posture as run_shadow_tracker_step().
            log.error("Fib Pivot shadow trade %s: tracker step failed (%s: %s) -- continuing "
                      "with remaining trades; this one will be retried next run.",
                      trade_id, type(exc).__name__, exc)


if __name__ == "__main__":
    main()
