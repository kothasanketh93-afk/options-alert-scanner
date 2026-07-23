"""
btst_tracker.py -- BTST exit-day tracking.
---------------------------------------------
Lifecycle (simpler than tracker.py's intraday one -- there's no
WAITING_ENTRY phase: a BTST position is created already OPEN, entered
at the prior evening's EOD quote):

  OPEN -> T1_HIT -> T2_HIT
  OPEN -> SL_HIT | GAP_SL_HIT
  OPEN -> FORCE_EXIT  (neither hit by BTST_FORCE_EXIT_HHMM)

GAP_SL_HIT is the one genuinely new case versus tracker.py: if the
exit day's FIRST candle already closes/opens beyond the stop loss, the
configured stop_loss price was never actually fillable -- this exits
at that candle's OPEN (the most defensible fill-price estimate for a
gap, same reasoning tracker.py's own docstring gives for using candle
close on intraday entries: a simple, defensible choice given only
OHLC, not a claim about the real fill).
"""

from datetime import datetime, timezone, timedelta

import btst_config

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


def _is_force_exit_time(dt: datetime) -> bool:
    return (dt.hour, dt.minute) >= btst_config.BTST_FORCE_EXIT_HHMM


def process_position_candles(position: dict, candles: list) -> list:
    """
    Mutates `position` in place, same contract as
    tracker.process_trade_candles(): caller passes only candles
    strictly after position["last_processed_ts"], sorted ascending.
    Returns a list of event dicts to notify on.
    """
    events = []
    is_first_candle_this_call = position.get("_gap_checked") is not True

    for i, candle in enumerate(candles):
        if position["status"] != "OPEN":
            position["last_processed_ts"] = candle["timestamp"]
            continue

        ts = _parse_ts(candle["timestamp"])
        o, h, l, c = candle["open"], candle["high"], candle["low"], candle["close"]
        entry_price = position["tracked_entry_price"]
        sl = position["stop_loss"]
        t1 = position["target_1"]
        t2 = position["target_2"]

        # --- Gap check: only on the very first candle this position
        # has ever seen on the exit day, per btst_config.BTST_GAP_CHECK_ENABLED.
        if btst_config.BTST_GAP_CHECK_ENABLED and is_first_candle_this_call and i == 0:
            position["_gap_checked"] = True
            if o <= sl:
                position["status"] = "GAP_SL_HIT"
                position["final_status"] = "GAP_SL_HIT"
                position["sl_hit_time"] = ts.isoformat()
                position["exit_price"] = o
                events.append({"type": "GAP_SL_HIT", "position": position, "candle": candle})
                position["last_processed_ts"] = candle["timestamp"]
                continue

        position["highest_since_entry"] = max(position.get("highest_since_entry", entry_price), h)
        position["lowest_since_entry"] = min(position.get("lowest_since_entry", entry_price), l)
        position["max_favorable_pct"] = max(
            position.get("max_favorable_pct", 0.0), _pct_move(entry_price, position["highest_since_entry"])
        )
        position["max_adverse_pct"] = min(
            position.get("max_adverse_pct", 0.0), _pct_move(entry_price, position["lowest_since_entry"])
        )

        hit_sl = l <= sl
        hit_t1 = h >= t1 and not position["t1_hit"]
        hit_t2 = h >= t2

        if hit_sl and (hit_t1 or hit_t2):
            position["status"] = "AMBIGUOUS"
            position["final_status"] = "AMBIGUOUS"
            events.append({"type": "AMBIGUOUS", "position": position, "candle": candle})
            position["last_processed_ts"] = candle["timestamp"]
            continue

        if hit_t1:
            position["t1_hit"] = True
            position["t1_hit_time"] = ts.isoformat()
            events.append({"type": "T1_HIT", "position": position})
            # keep tracking toward T2, same convention as tracker.py

        if hit_t2:
            position["status"] = "T2_HIT"
            position["final_status"] = "T2_HIT"
            position["t2_hit_time"] = ts.isoformat()
            position["exit_price"] = t2
            events.append({"type": "T2_HIT", "position": position})
            position["last_processed_ts"] = candle["timestamp"]
            continue

        if hit_sl:
            position["status"] = "SL_HIT"
            position["final_status"] = "T1_THEN_SL" if position["t1_hit"] else "SL_HIT"
            position["sl_hit_time"] = ts.isoformat()
            position["exit_price"] = sl
            events.append({"type": "SL_HIT", "position": position})
            position["last_processed_ts"] = candle["timestamp"]
            continue

        if _is_force_exit_time(ts):
            position["status"] = "FORCE_EXIT"
            position["final_status"] = "T1_THEN_FORCE_EXIT" if position["t1_hit"] else "FORCE_EXIT"
            position["exit_price"] = c
            events.append({"type": "FORCE_EXIT", "position": position})

        position["last_processed_ts"] = candle["timestamp"]

    return events
