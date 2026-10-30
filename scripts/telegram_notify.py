"""
telegram_notify.py -- V2.0
---------------------------
Sends formatted Telegram messages for each lifecycle event.
Idempotency is enforced by the caller (main.py) via a `messages_sent`
list on each trade — this module only formats and sends.

V2.0 CHANGES (superseded, see PHASE 3 below): no raw numeric score or
probability percentage ever appeared in a message. Trade Grade
(A+/A/B+/B), star-based Confidence, and emoji Risk Rating replaced
them.

PHASE 3 CHANGE (explicit decision, reversing the above): fmt_new_alert()
now shows the numeric Trade Score, Probability %, Confidence /10,
Risk /10, Reward /10, and Expected R:R, per trade_score.py's engine --
see that module's docstring for why. Grade/stars/risk-emoji are kept
alongside the numbers as a backward-compatible companion display, not
a replacement for them.

Every new-trade alert includes a "Why Recommended?" checklist and an
expected holding-time range. If alternatives passed the quality gate
too, they're listed as text only -- never opened as separate trades.
"""

from datetime import datetime
import os

from auth import get_telegram_credentials
from retry_utils import request_with_retry
from exceptions import TelegramFailure
from logging_setup import get_logger

DOCUMENT_API_URL = "https://api.telegram.org/bot{token}/sendDocument"

API_URL = f"https://api.telegram.org/bot{{token}}/sendMessage"
log = get_logger("telegram")


def _time_only(iso_ts: str) -> str:
    """
    tracker.py stores every timestamp (actual_entry_time, t1_hit_time,
    t2_hit_time, sl_hit_time) as a full `datetime.isoformat()` string
    -- date, seconds/microseconds, and the "+05:30" offset included
    (e.g. "2026-07-17T15:05:41.234567+05:30") -- because
    fmt_eod_closed()'s holding-duration math needs the full timestamp
    to subtract. Telegram messages only need the clock time, so this
    formats down to "HH:MM" for display without changing what's
    actually stored. Falls back to returning the raw string unchanged
    if it isn't a parseable ISO timestamp, so a malformed value shows
    up visibly instead of crashing message formatting.
    """
    try:
        return datetime.fromisoformat(iso_ts).strftime("%H:%M")
    except (TypeError, ValueError):
        return str(iso_ts)


def _trade_header(trade: dict) -> str:
    """Shared 'SYMBOL STRIKE DIRECTION (MONEYNESS)' line repeated across
    every lifecycle message below -- one definition instead of six copies
    that could silently drift out of sync with each other."""
    return f"{trade['symbol']} {trade['strike']} {trade['direction']} ({trade['moneyness']})"

# Retry policy (thresholds in config.py, logic in retry_utils.py) is
# unchanged from before this was centralized: only transient failures
# (network blips, rate limits, momentary 5xx) are retried; a malformed
# message still fails fast.


def send(text: str):
    bot_token, chat_id = get_telegram_credentials()
    url = API_URL.format(token=bot_token)
    try:
        resp = request_with_retry(
            "POST", url, error_cls=TelegramFailure, timeout=15,
            data={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
        )
    except TelegramFailure:
        log.error("Telegram send failed after retries (message len=%d).", len(text))
        raise
    log.info("Telegram message sent (len=%d).", len(text))
    return resp.json()


def send_document(file_path: str, caption: str = None):
    """
    Sends a FILE (not a text message) to the configured chat via
    Telegram's sendDocument endpoint -- added 2026-08-13 so
    scan_log_store.py's on-demand export can be delivered straight to
    the same Telegram chat alerts already go to, instead of only being
    reachable via a GitHub Actions Artifact download. Reuses the same
    credentials/retry policy/error type as send() above -- this is a
    second endpoint on the same bot, not a separate integration.

    caption: optional, shown under the file in Telegram (max ~1024
    chars per Telegram's own limit -- not enforced here, an overly long
    one is Telegram's 4xx to reject, not retried, same as any other
    malformed-request failure per request_with_retry()'s own policy).

    Telegram bots cap uploads at 50MB -- a file over that limit fails
    here with a TelegramFailure (Telegram's own 4xx rejection, not
    retried) rather than being silently truncated or skipped.
    """
    bot_token, chat_id = get_telegram_credentials()
    url = DOCUMENT_API_URL.format(token=bot_token)
    with open(file_path, "rb") as f:
        try:
            resp = request_with_retry(
                "POST", url, error_cls=TelegramFailure, timeout=60,
                data={"chat_id": chat_id, "caption": caption or ""},
                files={"document": (os.path.basename(file_path), f)},
            )
        except TelegramFailure:
            log.error("Telegram document send failed after retries (%s).", file_path)
            raise
    log.info("Telegram document sent: %s", file_path)
    return resp.json()


def _fmt_alternatives(alternatives: list, max_shown: int = 5) -> str:
    """
    Only the top `max_shown` alternatives are listed individually --
    changed 2026-07-21 after live feedback that a full 15-20 line
    list (every gate-passing candidate, most identical grade) in
    EVERY single alert message was the single biggest source of
    clutter. Anything beyond max_shown is summarized as a count
    instead of dropped silently, so the full picture (how many other
    setups also passed) is still visible, just not itemized.

    Caller (main.py) already deduplicates by symbol+direction before
    this -- ITM/ATM/OTM of the SAME symbol+direction used to all show
    up here as if they were 3 separate opportunities, when they're
    really 3 strike choices for the same underlying signal. By the
    time a list reaches this function, each entry is a genuinely
    different symbol+direction.
    """
    if not alternatives:
        return ""
    shown, rest = alternatives[:max_shown], alternatives[max_shown:]
    lines = [
        f"  • {a['symbol']} {a['direction']} {a['moneyness']} — "
        f"{a.get('trade_score', '?')}/100 (Grade {a['grade']})"
        for a in shown
    ]
    block = "\nAlternatives considered (not opened):\n" + "\n".join(lines)
    if rest:
        block += f"\n  …and {len(rest)} more"
    return block


def fmt_new_alert(trade: dict) -> str:
    """
    Phase 3: shows the numeric Trade Score / Probability / Confidence /
    Risk / Reward / Expected R:R block, per the phase-3 spec's example
    Telegram output -- an explicit decision reversing the earlier V2.0
    "no raw numeric score or probability ever reaches Telegram" design
    (see trade_score.py's module docstring). Grade/stars/risk-emoji are
    still shown too, as a backward-compatible companion to the numbers,
    not a replacement for them.
    """
    low_min, high_min = trade["expected_holding_minutes"]
    why = "\n".join(trade.get("why_reasons", [])) or "—"
    alt_block = _fmt_alternatives(trade.get("alternatives", []))
    direction_emoji = "🟢" if trade["direction"] == "CE" else "🔴"
    checklist = "\n".join(trade.get("factor_checklist", [])) or "—"
    # Phase 4: signed per-factor points breakdown ("+15 Trend Alignment",
    # "-5 Liquidity", ...) -- see trade_score.scoring_reasons().
    breakdown = "\n".join(trade.get("score_breakdown", [])) or "—"
    prob_label = trade.get("probability_label", "")
    prob_trend = trade.get("probability_trend", "Stable")

    return (
        f"🏆 BEST OPPORTUNITY — {trade['display_label']}\n\n"
        f"Trade ID: {trade['trade_id']}\n"
        f"Symbol: {trade['symbol']} | Direction: {trade['direction']} | "
        f"Strike: {trade['moneyness']}\n"
        f"Session: {trade['session']}\n"
        f"Spot: ₹{trade['spot_price']}\n"
        f"Strike Price: {trade['strike']} | Expiry: {trade['expiry']}\n\n"
        f"━━━━━━━━━━━━━━\n"
        f"{direction_emoji} {'BUY CE' if trade['direction'] == 'CE' else 'BUY PE'} SIGNAL\n"
        f"Trade Score : {trade['trade_score']}/100\n"
        f"Probability : {trade['probability_pct']}% ({prob_label}, {prob_trend})\n"
        f"Grade : {trade['grade']}\n"
        f"Confidence : {trade['confidence_score']}/10\n"
        f"Risk : {trade['risk_score']}/10\n"
        f"Reward : {trade['reward_score']}/10\n"
        f"Expected RR : {trade['expected_rr']}\n"
        f"━━━━━━━━━━━━━━\n"
        f"{checklist}\n"
        f"━━━━━━━━━━━━━━\n"
        f"Score Breakdown:\n{breakdown}\n"
        f"━━━━━━━━━━━━━━\n\n"
        f"Premium: ₹{trade['alert_premium']}\n"
        f"Entry Zone: ₹{trade['entry_low']} - ₹{trade['entry_high']}\n"
        f"Stop Loss: ₹{trade['stop_loss']}\n"
        f"Target 1: ₹{trade['target_1']}\n"
        f"Target 2: ₹{trade['target_2']}\n\n"
        f"Expected Holding: {low_min}-{high_min} minutes\n\n"
        f"Why Recommended?\n{why}\n"
        f"{alt_block}\n\n"
        f"Status: WAITING_ENTRY"
    )


def fmt_early_breakout_alert(trade: dict) -> str:
    """
    Upgraded 2026-07-24 (Institutional Accumulation spec's Fix 13
    layout) -- IN PLACE, same function, not a second formatter. Now
    leads with 🚨 EARLY INSTITUTIONAL BREAKOUT and adds the Breakout
    Price / Entry Zone / Maximum Entry (the Early Entry Window) plus
    Institutional / Base / Momentum sub-scores alongside the composite
    Score, so the message shows WHY each stage passed, not just the
    final number. Deliberately still a DIFFERENT message shape from
    fmt_new_alert() above (unchanged, for the existing Trade Score
    Engine) so the two are never confused in the channel.
    """
    reasons = trade.get("early_breakout_reasons", {})
    low_min, high_min = trade["expected_holding_minutes"]

    return (
        f"🚨 EARLY INSTITUTIONAL BREAKOUT — {trade['display_label']}\n\n"
        f"Trade ID: {trade['trade_id']}\n"
        f"Underlying: {trade['symbol']}\n"
        f"Direction: {trade['direction']}\n"
        f"Option: {trade['direction']} {trade['strike']}\n"
        f"Expiry: {trade['expiry']}\n"
        f"Premium: ₹{trade['alert_premium']}\n\n"
        f"Breakout Price: ₹{trade.get('breakout_price', trade['alert_premium'])}\n"
        f"Entry Zone: {trade.get('entry_zone', '—')}\n"
        f"Maximum Entry: ₹{trade.get('maximum_entry', '—')}\n\n"
        f"Stop Loss: ₹{trade['stop_loss']}\n"
        f"Target 1: ₹{trade['target_1']}\n"
        f"Target 2: ₹{trade['target_2']}\n"
        f"Risk Reward: {trade['expected_rr']}\n"
        f"Probability: {trade['probability_pct']}%\n"
        f"Score: {trade['trade_score']}/100 ({trade['grade']})\n"
        f"Institutional Score: {trade.get('institutional_score', '—')}/100\n"
        f"Base Score: {trade.get('base_score', '—')}/100\n"
        f"Momentum Score: {trade.get('momentum_score', '—')}/100\n\n"
        f"━━━━━━━━━━━━━━\n"
        f"Reasons\n"
        f"Trend: {reasons.get('trend', '—')}\n"
        f"Market Context: {reasons.get('market_context', '—')}\n"
        f"VWAP: {reasons.get('vwap', '—')}\n"
        f"Institutional: {reasons.get('institutional', '—')}\n"
        f"Base: {reasons.get('base', '—')}\n"
        f"Breakout: {reasons.get('breakout', '—')}\n"
        f"Follow-through: {reasons.get('follow_through', '—')}\n"
        f"Volume: {reasons.get('volume', '—')}\n"
        f"Liquidity: {reasons.get('liquidity', '—')}\n"
        f"Momentum: {reasons.get('momentum', '—')}\n"
        f"Fair Value Gap: {reasons.get('fair_value_gap', '—')}\n"
        f"Price Action: {reasons.get('price_action', '—')}\n"
        f"Smart Money: {reasons.get('smart_money', '—')}\n"
        f"━━━━━━━━━━━━━━\n\n"
        f"Expected Holding: {low_min}-{high_min} minutes\n\n"
        f"Invalidation: {reasons.get('invalidation', '—')}\n\n"
        f"Status: WAITING_ENTRY"
    )


def fmt_entry_activated(trade: dict) -> str:
    return (
        f"ENTRY ACTIVATED — {trade['display_label']}\n\n"
        f"{_trade_header(trade)}\n"
        f"Entry Time: {_time_only(trade['actual_entry_time'])}\n"
        f"Tracked Entry: ₹{trade['tracked_entry_price']}\n\n"
        f"Target 1: ₹{trade['target_1']}\n"
        f"Target 2: ₹{trade['target_2']}\n"
        f"Stop Loss: ₹{trade['stop_loss']}\n\n"
        f"Status: ACTIVE"
    )


def fmt_t1_hit(trade: dict, time_taken_min: int) -> str:
    return (
        f"TARGET 1 HIT — {trade['display_label']}\n\n"
        f"{_trade_header(trade)}\n"
        f"Entry Time: {_time_only(trade['actual_entry_time'])}\n"
        f"T1 Hit Time: {_time_only(trade['t1_hit_time'])}\n"
        f"Time Taken: {time_taken_min} minutes\n\n"
        f"Tracked Entry: ₹{trade['tracked_entry_price']}\n"
        f"T1: ₹{trade['target_1']}\n"
        f"Highest Reached: ₹{trade['highest_since_entry']}\n\n"
        f"Move from Entry: {trade['max_favorable_pct']:+.2f}%\n\n"
        f"Status: T1 SUCCESS\n"
        f"Tracking continues for T2."
    )


def fmt_t2_hit(trade: dict, time_taken_min: int) -> str:
    return (
        f"TARGET 2 HIT — {trade['display_label']}\n\n"
        f"{_trade_header(trade)}\n"
        f"Entry Time: {_time_only(trade['actual_entry_time'])}\n"
        f"T2 Hit Time: {_time_only(trade['t2_hit_time'])}\n"
        f"Time Taken: {time_taken_min} minutes\n\n"
        f"Tracked Entry: ₹{trade['tracked_entry_price']}\n"
        f"T2: ₹{trade['target_2']}\n"
        f"Highest Reached: ₹{trade['highest_since_entry']}\n\n"
        f"Move from Entry: {trade['max_favorable_pct']:+.2f}%\n\n"
        f"Final Result: T2 SUCCESS"
    )


def fmt_trailing_sl_adjusted(trade: dict) -> str:
    """2026-07-31: notifies when tracker.py's trailing-stop feature
    (config.TRAILING_STOP_ENABLED) raises a trade's SL to lock in
    profit after a favorable move -- see tracker.py's
    _maybe_apply_trailing_stop() for the trigger logic."""
    return (
        f"🔒 SL RAISED (Trailing) — {trade['display_label']}\n\n"
        f"{_trade_header(trade)}\n"
        f"Tracked Entry: ₹{trade['tracked_entry_price']}\n"
        f"Best Move So Far: {trade['max_favorable_pct']:+.2f}%\n\n"
        f"SL moved: ₹{trade.get('trailing_stop_moved_to')} "
        f"(locking in a small profit instead of the original stop)"
    )


def fmt_sl_hit(trade: dict, time_taken_min: int) -> str:
    result = "T1 achieved, SL after T2 attempt" if trade["t1_hit"] else "FAILED"
    return (
        f"STOP LOSS HIT — {trade['display_label']}\n\n"
        f"{_trade_header(trade)}\n"
        f"Entry Time: {_time_only(trade['actual_entry_time'])}\n"
        f"SL Time: {_time_only(trade['sl_hit_time'])}\n"
        f"Time Taken: {time_taken_min} minutes\n\n"
        f"Tracked Entry: ₹{trade['tracked_entry_price']}\n"
        f"SL: ₹{trade['stop_loss']}\n\n"
        f"Highest Before SL: ₹{trade['highest_since_entry']}\n"
        f"Best Move Before SL: {trade['max_favorable_pct']:+.2f}%\n\n"
        f"Final Result: {result}"
    )


def fmt_not_entered(trade: dict) -> str:
    return (
        f"NOT ENTERED (EOD) — {trade['display_label']}\n\n"
        f"{_trade_header(trade)}\n"
        f"Entry Zone: ₹{trade['entry_low']} - ₹{trade['entry_high']}\n"
        f"Never touched during market hours.\n\n"
        f"Status: NOT_ENTERED"
    )


def fmt_eod_closed(trade: dict) -> str:
    return (
        f"EOD — TRADE UNRESOLVED — {trade['display_label']}\n"
        f"{_trade_header(trade)}\n"
        f"Entry: ₹{trade['tracked_entry_price']} at {_time_only(trade['actual_entry_time'])}\n"
        f"Last Price: ₹{trade.get('eod_last_price', 'N/A')}\n\n"
        f"Max Favorable: {trade['max_favorable_pct']:+.2f}%\n"
        f"Max Adverse: {trade['max_adverse_pct']:+.2f}%\n\n"
        f"Status: EOD_CLOSED (neither SL nor target reached)"
    )


def fmt_ambiguous(trade: dict, candle: dict) -> str:
    return (
        f"AMBIGUOUS CANDLE — MANUAL REVIEW NEEDED — {trade['display_label']}\n"
        f"{_trade_header(trade)}\n"
        f"Both SL and a target level were touched within one 1-min "
        f"candle; order can't be determined from OHLC.\n\n"
        f"Candle: O {candle['open']} H {candle['high']} "
        f"L {candle['low']} C {candle['close']} @ {candle['timestamp']}\n\n"
        f"SL: ₹{trade['stop_loss']} | T1: ₹{trade['target_1']} | T2: ₹{trade['target_2']}\n\n"
        f"Status: AMBIGUOUS (not counted as success or failure)"
    )


def fmt_circuit_breaker(max_consecutive_sl: int) -> str:
    return (
        f"CIRCUIT BREAKER TRIPPED\n\n"
        f"{max_consecutive_sl} consecutive SL hits today. "
        f"New trade alerts paused for the rest of the session.\n\n"
        f"Already-open trades continue being tracked normally."
    )


def _holding_minutes(trade: dict):
    """Minutes between actual entry and whichever resolution timestamp
    applies (T2, then SL, else None if unresolved/never entered)."""
    entry = trade.get("actual_entry_time")
    if not entry:
        return None
    exit_ts = trade.get("t2_hit_time") or trade.get("sl_hit_time")
    if not exit_ts:
        return None
    return int((datetime.fromisoformat(exit_ts) - datetime.fromisoformat(entry)).total_seconds() // 60)


def fmt_daily_summary(date_str: str, trades: list, daily_stats: dict = None,
                       probability_stats: dict = None) -> str:
    entered = [t for t in trades if t["status"] not in ("WAITING_ENTRY", "NOT_ENTERED")]
    not_entered = [t for t in trades if t["status"] == "NOT_ENTERED"]
    t2 = [t for t in trades if t.get("final_status") == "T2_HIT"]
    # BUGFIX: this used to check status == "T1_HIT", which is the label for
    # a trade STILL OPEN and tracking toward T2 (non-terminal) -- not a
    # resolved outcome. That silently counted still-open trades as
    # "successes" in the win-rate math and hid them from the summary
    # entirely. The actual resolved "hit T1, never reached T2" outcome is
    # final_status == "T1_HIT_THEN_EOD".
    t1_only = [t for t in trades if t.get("final_status") == "T1_HIT_THEN_EOD"]
    sl = [t for t in trades if t.get("final_status") in ("SL_HIT", "T1_THEN_SL")]
    ambiguous = [t for t in trades if t["status"] == "AMBIGUOUS"]
    # BUGFIX: this used to filter on status == "EOD_CLOSED", but tracker.py
    # sets that SAME status for both a plain EOD timeout (final_status ==
    # "EOD_CLOSED") AND a trade that hit T1 first then timed out
    # (final_status == "T1_HIT_THEN_EOD") -- the latter is already counted
    # above in t1_only. Filtering by status alone double-counted every
    # T1_HIT_THEN_EOD trade in both the "T1 Success" line and the "EOD
    # Closed" line, inflating the EOD count and breaking the totals'
    # internal consistency. Filtering by final_status (like every other
    # bucket here) makes this mutually exclusive with t1_only.
    eod = [t for t in trades if t.get("final_status") == "EOD_CLOSED"]
    # Trades that haven't reached a terminal outcome yet as of summary time
    # (e.g. still ACTIVE/T1_HIT because the last candle hasn't been processed
    # yet) -- surfaced explicitly instead of silently vanishing from the math.
    still_open = [t for t in trades if t["status"] in ("WAITING_ENTRY", "ACTIVE", "T1_HIT")]

    resolved = len(t2) + len(t1_only) + len(sl)
    successes = len(t2) + len(t1_only)
    win_rate = f"{successes}/{resolved} = {successes/resolved*100:.2f}%" if resolved else "N/A"

    # Best trade by peak favorable move; worst by peak adverse move --
    # BUGFIX: "worst" used to also be defined via min(max_favorable_pct),
    # a stale leftover from before max_adverse_pct existed. That measured
    # "least gained" rather than "most lost," which is a materially
    # different (and less useful) notion of "worst."
    best_line = worst_line = "N/A"
    if entered:
        best_t = max(entered, key=lambda t: t.get("max_favorable_pct", 0.0))
        worst_t = min(entered, key=lambda t: t.get("max_adverse_pct", 0.0))
        best_line = f"{best_t['display_label']} ({best_t.get('max_favorable_pct', 0.0):+.2f}%)"
        worst_line = f"{worst_t['display_label']} ({worst_t.get('max_adverse_pct', 0.0):+.2f}%)"

    # Average holding time across trades with a clear T2/SL resolution.
    holding_times = [m for m in (_holding_minutes(t) for t in trades) if m is not None]
    avg_holding = f"{sum(holding_times) / len(holding_times):.0f} minutes" if holding_times else "N/A"

    # Medium priority (EOD daily statistics): Scanned Symbols / Valid
    # Signals / Rejected Signals / Average-Highest-Lowest Probability /
    # Broker Errors / API Failures. Both params default to None so any
    # OTHER future caller that only passes (date_str, trades) still
    # gets a valid message -- this section is just omitted for them,
    # not a KeyError.
    scan_block = ""
    if daily_stats:
        scan_block += (
            f"\nScanned Symbols: {daily_stats.get('scanned_symbols', 0)}\n"
            f"Valid Signals: {daily_stats.get('valid_signals', 0)}\n"
            f"Rejected Signals: {daily_stats.get('rejected_signals', 0)}\n"
            f"Broker Errors: {daily_stats.get('broker_errors', 0)}\n"
            f"API Failures: {daily_stats.get('api_failures', 0)}\n"
        )
    if probability_stats and probability_stats.get("average") is not None:
        scan_block += (
            f"Average Probability: {probability_stats['average']}%\n"
            f"Highest Probability: {probability_stats['highest']}%\n"
            f"Lowest Probability: {probability_stats['lowest']}%\n"
        )

    return (
        f"DAILY SCANNER SUMMARY\n\n"
        f"Date: {date_str}\n\n"
        f"Recommendations: {len(trades)}\n"
        f"Entered Trades: {len(entered)}\n"
        f"Not Entered: {len(not_entered)}\n\n"
        f"T2 Success: {len(t2)}\n"
        f"T1 Success (EOD before T2): {len(t1_only)}\n"
        f"SL Hit: {len(sl)}\n"
        f"Ambiguous: {len(ambiguous)}\n"
        f"EOD Closed: {len(eod)}\n"
        f"Still Open (unresolved): {len(still_open)}\n\n"
        f"Best Trade: {best_line}\n"
        f"Worst Trade: {worst_line}\n"
        f"Average Holding Time: {avg_holding}\n\n"
        f"Resolved Success Rate:\n{win_rate}\n"
        f"{scan_block}"
    )
