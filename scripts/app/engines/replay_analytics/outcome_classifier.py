"""
app/engines/replay_analytics/outcome_classifier.py -- Trade Outcome Engine.
------------------------------------------------------------------------------
Turns a signal_replay.replay_signal() result (plus the original trade
dict) into exactly one of the six required outcome labels:
    WIN, LOSS, BREAKEVEN, NO_ENTRY, TIME_EXIT, INVALID_SIGNAL
"""

from config import REPLAY_BREAKEVEN_BAND_PCT


def _pct_move(entry: float, exit_price: float) -> float:
    if not entry:
        return 0.0
    return (exit_price - entry) / entry


def classify_outcome(trade: dict, replay_result: dict) -> str:
    """
    trade: original signal dict (used only to sanity-check the signal
        itself is well-formed -- see INVALID_SIGNAL below).
    replay_result: the dict returned by signal_replay.replay_signal().
    """
    entry_low = trade.get("entry_low")
    entry_high = trade.get("entry_high")
    stop_loss = trade.get("stop_loss")
    target_1 = trade.get("target_1")

    # A signal missing the fields needed to even define a trade
    # (no entry zone, no stop, no first target, or SL on the wrong
    # side of the entry zone) never should have gone out -- flag it
    # rather than silently scoring it as a loss.
    if entry_low is None or entry_high is None or stop_loss is None or target_1 is None:
        return "INVALID_SIGNAL"
    if entry_low > entry_high:
        return "INVALID_SIGNAL"
    if stop_loss >= entry_low:
        return "INVALID_SIGNAL"

    reason = replay_result.get("exit_reason")

    if reason in ("NOT_ENTERED", "NO_CANDLES"):
        return "NO_ENTRY"

    if reason == "AMBIGUOUS":
        # Can't determine ordering from OHLC alone -- treated as a
        # time-based exit for analytics purposes rather than guessed
        # as a win or loss, same spirit as tracker.py's AMBIGUOUS status.
        return "TIME_EXIT"

    if reason == "EOD_CLOSED":
        entry = replay_result.get("entry")
        exit_price = replay_result.get("exit_price")
        if entry is None or exit_price is None:
            return "TIME_EXIT"
        move = _pct_move(entry, exit_price)
        if abs(move) <= REPLAY_BREAKEVEN_BAND_PCT:
            return "BREAKEVEN"
        return "WIN" if move > 0 else "LOSS"

    if reason == "SL_HIT":
        return "LOSS"

    if reason in ("T1_HIT", "T2_HIT", "T3_HIT"):
        return "WIN"

    return "TIME_EXIT"
