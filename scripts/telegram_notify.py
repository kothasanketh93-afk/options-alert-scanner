"""
telegram_notify.py -- V2.0
---------------------------
Sends formatted Telegram messages for each lifecycle event.
Idempotency is enforced by the caller (main.py) via a `messages_sent`
list on each trade — this module only formats and sends.

V2.0 CHANGES: no raw numeric score or probability percentage ever
appears in a message (per spec items REMOVE #1 and #5). Trade Grade
(A+/A/B+/B), star-based Confidence, and emoji Risk Rating replace
them. Every new-trade alert includes a "Why Recommended?" checklist
and an expected holding-time range. If alternatives passed the
quality gate too, they're listed as text only -- never opened as
separate trades.
"""

from auth import get_telegram_credentials
from retry_utils import request_with_retry
from exceptions import TelegramFailure
from logging_setup import get_logger

API_URL = f"https://api.telegram.org/bot{{token}}/sendMessage"
log = get_logger("telegram")

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


def _fmt_alternatives(alternatives: list) -> str:
    if not alternatives:
        return ""
    lines = [
        f"  • {a['symbol']} {a['direction']} {a['moneyness']} — Grade {a['grade']}"
        for a in alternatives
    ]
    return "\nAlternatives considered (not opened):\n" + "\n".join(lines)


def fmt_new_alert(trade: dict) -> str:
    low_min, high_min = trade["expected_holding_minutes"]
    why = "\n".join(trade.get("why_reasons", [])) or "—"
    alt_block = _fmt_alternatives(trade.get("alternatives", []))

    return (
        f"🏆 BEST OPPORTUNITY\n\n"
        f"Trade ID: {trade['trade_id']}\n"
        f"Symbol: {trade['symbol']} | Direction: {trade['direction']} | "
        f"Strike: {trade['moneyness']}\n"
        f"Session: {trade['session']}\n"
        f"Spot: ₹{trade['spot_price']}\n"
        f"Strike Price: {trade['strike']} | Expiry: {trade['expiry']}\n\n"
        f"Trade Grade: {trade['grade']}\n"
        f"Confidence: {trade['confidence_stars']}  {trade['confidence_label']}\n"
        f"Risk Rating: {trade['risk_rating']} {trade['risk_label']}\n\n"
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


def fmt_entry_activated(trade: dict) -> str:
    return (
        f"ENTRY ACTIVATED\n\n"
        f"Trade ID: {trade['trade_id']}\n"
        f"Entry Time: {trade['actual_entry_time']}\n"
        f"Tracked Entry: ₹{trade['tracked_entry_price']}\n\n"
        f"Target 1: ₹{trade['target_1']}\n"
        f"Target 2: ₹{trade['target_2']}\n"
        f"Stop Loss: ₹{trade['stop_loss']}\n\n"
        f"Status: ACTIVE"
    )


def fmt_t1_hit(trade: dict, time_taken_min: int) -> str:
    return (
        f"TARGET 1 HIT\n\n"
        f"Trade ID: {trade['trade_id']}\n\n"
        f"Entry Time: {trade['actual_entry_time']}\n"
        f"T1 Hit Time: {trade['t1_hit_time']}\n"
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
        f"TARGET 2 HIT\n\n"
        f"Trade ID: {trade['trade_id']}\n\n"
        f"Entry Time: {trade['actual_entry_time']}\n"
        f"T2 Hit Time: {trade['t2_hit_time']}\n"
        f"Time Taken: {time_taken_min} minutes\n\n"
        f"Tracked Entry: ₹{trade['tracked_entry_price']}\n"
        f"T2: ₹{trade['target_2']}\n"
        f"Highest Reached: ₹{trade['highest_since_entry']}\n\n"
        f"Move from Entry: {trade['max_favorable_pct']:+.2f}%\n\n"
        f"Final Result: T2 SUCCESS"
    )


def fmt_sl_hit(trade: dict, time_taken_min: int) -> str:
    result = "T1 achieved, SL after T2 attempt" if trade["t1_hit"] else "FAILED"
    return (
        f"STOP LOSS HIT\n\n"
        f"Trade ID: {trade['trade_id']}\n\n"
        f"Entry Time: {trade['actual_entry_time']}\n"
        f"SL Time: {trade['sl_hit_time']}\n"
        f"Time Taken: {time_taken_min} minutes\n\n"
        f"Tracked Entry: ₹{trade['tracked_entry_price']}\n"
        f"SL: ₹{trade['stop_loss']}\n\n"
        f"Highest Before SL: ₹{trade['highest_since_entry']}\n"
        f"Best Move Before SL: {trade['max_favorable_pct']:+.2f}%\n\n"
        f"Final Result: {result}"
    )


def fmt_not_entered(trade: dict) -> str:
    return (
        f"NOT ENTERED (EOD)\n\n"
        f"Trade ID: {trade['trade_id']}\n"
        f"Entry Zone: ₹{trade['entry_low']} - ₹{trade['entry_high']}\n"
        f"Never touched during market hours.\n\n"
        f"Status: NOT_ENTERED"
    )


def fmt_eod_closed(trade: dict) -> str:
    return (
        f"EOD — TRADE UNRESOLVED\n\n"
        f"Trade ID: {trade['trade_id']}\n"
        f"Entry: ₹{trade['tracked_entry_price']} at {trade['actual_entry_time']}\n"
        f"Last Price: ₹{trade.get('eod_last_price', 'N/A')}\n\n"
        f"Max Favorable: {trade['max_favorable_pct']:+.2f}%\n"
        f"Max Adverse: {trade['max_adverse_pct']:+.2f}%\n\n"
        f"Status: EOD_CLOSED (neither SL nor target reached)"
    )


def fmt_ambiguous(trade: dict, candle: dict) -> str:
    return (
        f"AMBIGUOUS CANDLE — MANUAL REVIEW NEEDED\n\n"
        f"Trade ID: {trade['trade_id']}\n"
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
    from datetime import datetime
    return int((datetime.fromisoformat(exit_ts) - datetime.fromisoformat(entry)).total_seconds() // 60)


def fmt_daily_summary(date_str: str, trades: list) -> str:
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
        best_line = f"{best_t['trade_id']} ({best_t.get('max_favorable_pct', 0.0):+.2f}%)"
        worst_line = f"{worst_t['trade_id']} ({worst_t.get('max_adverse_pct', 0.0):+.2f}%)"

    # Average holding time across trades with a clear T2/SL resolution.
    holding_times = [m for m in (_holding_minutes(t) for t in trades) if m is not None]
    avg_holding = f"{sum(holding_times) / len(holding_times):.0f} minutes" if holding_times else "N/A"

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
        f"Resolved Success Rate:\n{win_rate}"
    )
