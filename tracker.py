"""
tracker.py
----------
Processes 1-minute option candles chronologically against each open
trade's rules, per the required lifecycle:

  WAITING_ENTRY -> ACTIVE -> T1_HIT -> T2_HIT
  WAITING_ENTRY -> ACTIVE -> SL_HIT
  WAITING_ENTRY -> NOT_ENTERED   (entry zone never touched by close)
  ACTIVE -> EOD_CLOSED           (neither SL nor target reached by close)
  * -> AMBIGUOUS                 (SL and target both touched in one
                                   candle with no way to order them)

This module is intentionally decoupled from the Upstox client: it takes
a list of already-fetched candle dicts and a market-close cutoff, and
returns events to notify + the mutated trade dict. main.py wires in the
actual API calls.

Candle dict shape expected:
{
  "timestamp": "2026-07-13T09:43:00+05:30",
  "open": 120.1, "high": 122.4, "low": 119.8, "close": 121.9
}
"""

from datetime import datetime, timezone, timedelta

from config import MARKET_CLOSE_HHMM

IST = timezone(timedelta(hours=5, minutes=30))


def _parse_ts(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    return dt


def _pct_move(entry_price: float, price: float) -> float:
    if entry_price == 0:
        return 0.0
    return round((price - entry_price) / entry_price * 100, 2)


def _is_after_market_close(dt: datetime) -> bool:
    return (dt.hour, dt.minute) >= MARKET_CLOSE_HHMM


def process_trade_candles(trade: dict, candles: list) -> list:
    """
    Mutates `trade` in place, advancing it through candles in
    chronological order. Returns a list of event dicts to notify on,
    e.g. [{"type": "ENTRY_ACTIVATED", ...}, {"type": "T1_HIT", ...}]

    Caller is responsible for:
      - only passing candles strictly after trade["last_processed_ts"]
      - sorting candles ascending by timestamp before calling
      - calling this once per trade per run
    """
    events = []

    for candle in candles:
        ts = _parse_ts(candle["timestamp"])
        o, h, l, c = candle["open"], candle["high"], candle["low"], candle["close"]

        if trade["status"] == "WAITING_ENTRY":
            entry_low, entry_high = trade["entry_low"], trade["entry_high"]
            touched_entry = (l <= entry_high) and (h >= entry_low)

            if touched_entry:
                # Use the candle's close as the reference entry price
                # (a simple, defensible choice — avoids assuming intra-
                # candle fill price we can't actually observe with OHLC).
                entry_price = c
                trade["status"] = "ACTIVE"
                trade["actual_entry_time"] = ts.isoformat()
                trade["tracked_entry_price"] = entry_price
                trade["highest_since_entry"] = entry_price
                trade["lowest_since_entry"] = entry_price
                trade["max_favorable_pct"] = 0.0
                trade["max_adverse_pct"] = 0.0
                events.append({
                    "type": "ENTRY_ACTIVATED",
                    "trade": trade,
                    "candle_ts": ts.isoformat(),
                })
                # fall through: same candle can also move toward T1/SL,
                # but we conservatively wait for the *next* candle to
                # evaluate T1/SL so entry isn't judged on the same bar
                # it triggered on. This avoids a second ambiguity source.
                trade["last_processed_ts"] = candle["timestamp"]
                continue

            elif _is_after_market_close(ts):
                trade["status"] = "NOT_ENTERED"
                events.append({"type": "NOT_ENTERED", "trade": trade})

            trade["last_processed_ts"] = candle["timestamp"]
            continue

        if trade["status"] == "ACTIVE":
            entry_price = trade["tracked_entry_price"]
            trade["highest_since_entry"] = max(trade["highest_since_entry"], h)
            trade["lowest_since_entry"] = min(trade["lowest_since_entry"], l)
            trade["max_favorable_pct"] = max(
                trade["max_favorable_pct"], _pct_move(entry_price, trade["highest_since_entry"])
            )
            trade["max_adverse_pct"] = min(
                trade["max_adverse_pct"], _pct_move(entry_price, trade["lowest_since_entry"])
            )

            sl = trade["stop_loss"]
            t1 = trade["target_1"]
            t2 = trade["target_2"]

            hit_sl = l <= sl
            hit_t1 = h >= t1 and not trade["t1_hit"]
            hit_t2 = h >= t2

            # --- Ambiguity check: both SL and (T1 or T2) touched in
            # the same candle, with no way to determine order from OHLC.
            if hit_sl and (hit_t1 or hit_t2):
                trade["status"] = "AMBIGUOUS"
                trade["final_status"] = "AMBIGUOUS"
                events.append({
                    "type": "AMBIGUOUS",
                    "trade": trade,
                    "candle": candle,
                })
                trade["last_processed_ts"] = candle["timestamp"]
                continue

            if hit_t1 and not trade["t1_hit"]:
                trade["t1_hit"] = True
                trade["t1_hit_time"] = ts.isoformat()
                trade["status"] = "T1_HIT"
                events.append({"type": "T1_HIT", "trade": trade})
                # keep tracking for T2 — do not `continue` out of ACTIVE-like
                # processing; status label T1_HIT is informational, tracking
                # continues below on subsequent candles.

            if hit_t2:
                trade["t2_hit"] = True
                trade["t2_hit_time"] = ts.isoformat()
                trade["status"] = "T2_HIT"
                trade["final_status"] = "T2_HIT"
                events.append({"type": "T2_HIT", "trade": trade})
                trade["last_processed_ts"] = candle["timestamp"]
                continue

            if hit_sl:
                trade["sl_hit_time"] = ts.isoformat()
                trade["status"] = "SL_HIT"
                trade["final_status"] = "T1_THEN_SL" if trade["t1_hit"] else "SL_HIT"
                events.append({"type": "SL_HIT", "trade": trade})
                trade["last_processed_ts"] = candle["timestamp"]
                continue

            if _is_after_market_close(ts):
                trade["status"] = "EOD_CLOSED"
                trade["final_status"] = "EOD_CLOSED"
                trade["eod_last_price"] = c
                events.append({"type": "EOD_CLOSED", "trade": trade})

            trade["last_processed_ts"] = candle["timestamp"]
            continue

        # T1_HIT status: still actively tracking toward T2, same logic as ACTIVE
        if trade["status"] == "T1_HIT":
            trade["highest_since_entry"] = max(trade["highest_since_entry"], h)
            trade["lowest_since_entry"] = min(trade["lowest_since_entry"], l)
            entry_price = trade["tracked_entry_price"]
            trade["max_favorable_pct"] = max(
                trade["max_favorable_pct"], _pct_move(entry_price, trade["highest_since_entry"])
            )
            trade["max_adverse_pct"] = min(
                trade["max_adverse_pct"], _pct_move(entry_price, trade["lowest_since_entry"])
            )

            sl = trade["stop_loss"]
            t2 = trade["target_2"]
            hit_sl = l <= sl
            hit_t2 = h >= t2

            if hit_sl and hit_t2:
                trade["status"] = "AMBIGUOUS"
                trade["final_status"] = "T1_HIT_THEN_AMBIGUOUS"
                events.append({"type": "AMBIGUOUS_AFTER_T1", "trade": trade, "candle": candle})
                trade["last_processed_ts"] = candle["timestamp"]
                continue

            if hit_t2:
                trade["t2_hit"] = True
                trade["t2_hit_time"] = ts.isoformat()
                trade["status"] = "T2_HIT"
                trade["final_status"] = "T2_HIT"
                events.append({"type": "T2_HIT", "trade": trade})
                trade["last_processed_ts"] = candle["timestamp"]
                continue

            if hit_sl:
                trade["sl_hit_time"] = ts.isoformat()
                trade["status"] = "SL_HIT"
                trade["final_status"] = "T1_THEN_SL"
                events.append({"type": "SL_HIT", "trade": trade})
                trade["last_processed_ts"] = candle["timestamp"]
                continue

            if _is_after_market_close(ts):
                trade["status"] = "EOD_CLOSED"
                trade["final_status"] = "T1_HIT_THEN_EOD"
                trade["eod_last_price"] = c
                events.append({"type": "EOD_CLOSED", "trade": trade})

            trade["last_processed_ts"] = candle["timestamp"]
            continue

        # Terminal states: nothing more to do
        trade["last_processed_ts"] = candle["timestamp"]

    return events


def compute_time_taken(entry_iso: str, event_iso: str) -> int:
    """Minutes between entry activation and an event, for messages/summary."""
    if not entry_iso or not event_iso:
        return None
    entry = _parse_ts(entry_iso)
    event = _parse_ts(event_iso)
    return int((event - entry).total_seconds() // 60)
