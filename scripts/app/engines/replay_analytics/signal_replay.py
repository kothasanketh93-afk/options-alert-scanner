"""
app/engines/replay_analytics/signal_replay.py -- Signal Replay Engine.
------------------------------------------------------------------------------
Replays ONE historical signal against its own candle history (any
timeframe from replay_engine.resample_candles()) and determines exactly
what would have happened: Entry, Stop Loss, Target 1/2/3, Time to
Target, Time to Stop Loss, Maximum Profit, Maximum Drawdown, Exit
Reason.

This mirrors tracker.process_trade_candles()'s lifecycle rules
(WAITING_ENTRY -> ACTIVE -> T1_HIT -> T2_HIT -> SL_HIT, NOT_ENTERED,
EOD_CLOSED, AMBIGUOUS) but is a SEPARATE, read-only function -- it
never mutates a live trade dict or emits tracker's notify events, and
it adds the Target 3 leg + timing/drawdown fields Part 7 needs that
the live tracker has no reason to compute in real time. Reusing
tracker.py's exact entry/SL/target semantics (rather than inventing
new ones) is what makes "replay the scanner exactly as it would have
behaved live" true.

Trade dict fields read (all already produced by scanner.py /
decision_engine for a real signal -- nothing new required upstream):
    entry_low, entry_high   -- entry zone (fill = first candle whose
                               range touches this zone)
    stop_loss
    target_1, target_2, target_3 (target_3 optional -- many existing
                               signals only carry two targets; this
                               engine tolerates its absence)
    direction               -- "CE"/"PE" or "BUY"/"SELL"-style intent;
                               only used for logging/labelling here,
                               since entry/SL/targets are already
                               absolute premium levels regardless of
                               direction (matches tracker.py's convention)
"""

from datetime import datetime, timezone, timedelta

from config import MARKET_CLOSE_HHMM

IST = timezone(timedelta(hours=5, minutes=30))


def parse_ts(ts) -> datetime:
    dt = ts if isinstance(ts, datetime) else datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    return dt


def _is_after_close(dt) -> bool:
    return (dt.hour, dt.minute) >= MARKET_CLOSE_HHMM


def replay_signal(trade: dict, candles: list) -> dict:
    """
    Returns a result dict:
      entry, stop_loss, target_1, target_2, target_3 (echoed from the
        input, target_3 may be None),
      entered (bool), entry_time, entry_candle_index,
      exit_reason: one of "T1_HIT", "T2_HIT", "T3_HIT", "SL_HIT",
        "NOT_ENTERED", "EOD_CLOSED", "AMBIGUOUS", "NO_CANDLES",
      time_to_target (minutes from signal candle to first target hit,
        or None), time_to_stop_loss (minutes to SL hit, or None),
      max_profit_pct, max_drawdown_pct (both relative to entry, over
        the life of the trade -- 0.0 if never entered),
      exit_price, exit_time (last relevant candle's timestamp).

    Candles must be chronologically sorted and start at/after the
    signal candle (caller's responsibility -- same contract as
    tracker.process_trade_candles()).
    """
    entry_low = trade.get("entry_low")
    entry_high = trade.get("entry_high")
    stop_loss = trade.get("stop_loss")
    target_1 = trade.get("target_1")
    target_2 = trade.get("target_2")
    target_3 = trade.get("target_3")

    result = {
        "entry": None, "stop_loss": stop_loss,
        "target_1": target_1, "target_2": target_2, "target_3": target_3,
        "entered": False, "entry_time": None, "entry_candle_index": None,
        "exit_reason": "NO_CANDLES", "time_to_target": None,
        "time_to_stop_loss": None, "max_profit_pct": 0.0,
        "max_drawdown_pct": 0.0, "exit_price": None, "exit_time": None,
    }

    if not candles:
        return result

    signal_time = parse_ts(candles[0]["timestamp"])
    entered = False
    entry_price = None
    entry_time = None
    highest_since_entry = None
    lowest_since_entry = None
    last_hit_target_field = None
    last_hit_target_value = None

    for idx, candle in enumerate(candles):
        ts = parse_ts(candle["timestamp"])
        lo, hi, close = candle["low"], candle["high"], candle["close"]

        if not entered:
            if entry_low is not None and entry_high is not None and lo <= entry_high and hi >= entry_low:
                entered = True
                # Fill at the tighter of (entry_high, hi) touching the zone,
                # same "conservative fill" convention as tracker.py: assume
                # the worse of the two bounds actually reachable this candle.
                entry_price = min(entry_high, max(entry_low, close))
                entry_time = ts
                highest_since_entry = hi
                lowest_since_entry = lo
                result["entered"] = True
                result["entry"] = entry_price
                result["entry_time"] = entry_time.isoformat()
                result["entry_candle_index"] = idx
            if _is_after_close(ts) and not entered:
                result["exit_reason"] = "NOT_ENTERED"
                result["exit_time"] = ts.isoformat()
                return result
            continue

        # entered -- track running extremes for max profit / drawdown
        highest_since_entry = max(highest_since_entry, hi)
        lowest_since_entry = min(lowest_since_entry, lo)

        sl_hit = stop_loss is not None and lo <= stop_loss
        t1_hit = target_1 is not None and hi >= target_1
        t2_hit = target_2 is not None and hi >= target_2
        t3_hit = target_3 is not None and hi >= target_3

        if sl_hit and (t1_hit or t2_hit or t3_hit):
            # Both touched in the same candle -- can't order them from
            # OHLC alone. Same "AMBIGUOUS" posture as tracker.py.
            result["exit_reason"] = "AMBIGUOUS"
            result["exit_price"] = stop_loss
            result["exit_time"] = ts.isoformat()
            result["time_to_stop_loss"] = round((ts - signal_time).total_seconds() / 60, 1)
            break

        if sl_hit:
            result["exit_reason"] = "SL_HIT"
            result["exit_price"] = stop_loss
            result["exit_time"] = ts.isoformat()
            result["time_to_stop_loss"] = round((ts - signal_time).total_seconds() / 60, 1)
            break

        if t3_hit:
            result["exit_reason"] = "T3_HIT"
            result["exit_price"] = target_3
            result["exit_time"] = ts.isoformat()
            result["time_to_target"] = round((ts - signal_time).total_seconds() / 60, 1)
            break
        if t2_hit and last_hit_target_field != "target_2":
            last_hit_target_field, last_hit_target_value = "target_2", target_2
            if target_3 is None:
                result["exit_reason"] = "T2_HIT"
                result["exit_price"] = target_2
                result["exit_time"] = ts.isoformat()
                result["time_to_target"] = round((ts - signal_time).total_seconds() / 60, 1)
                break
        elif t1_hit and last_hit_target_field is None:
            last_hit_target_field, last_hit_target_value = "target_1", target_1
            if target_2 is None:
                result["exit_reason"] = "T1_HIT"
                result["exit_price"] = target_1
                result["exit_time"] = ts.isoformat()
                result["time_to_target"] = round((ts - signal_time).total_seconds() / 60, 1)
                break

        if _is_after_close(ts):
            result["exit_reason"] = "EOD_CLOSED"
            result["exit_price"] = close
            result["exit_time"] = ts.isoformat()
            break
    else:
        # Loop completed without an explicit break (ran out of candles
        # mid-trade -- data ended before EOD/target/SL).
        if entered and result["exit_reason"] == "NO_CANDLES":
            last = candles[-1]
            result["exit_reason"] = "EOD_CLOSED"
            result["exit_price"] = last["close"]
            result["exit_time"] = parse_ts(last["timestamp"]).isoformat()

    if entered:
        result["max_profit_pct"] = round((highest_since_entry - entry_price) / entry_price * 100, 2) if entry_price else 0.0
        result["max_drawdown_pct"] = round((entry_price - lowest_since_entry) / entry_price * 100, 2) if entry_price else 0.0

    return result
