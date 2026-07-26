"""
app/engines/replay_analytics/false_signal.py -- False Signal Engine.
------------------------------------------------------------------------------
Detects, per alerted trade:
    - False Breakout / False Breakdown (price reversed hard against
      the signal direction shortly after)
    - Weak Momentum Entry (post-entry candles had little range/follow-through)
    - Low Quality Entry (scored just above the floor and lost)
    - Poor Option Selection (a losing trade whose options_intelligence
      score -- if present on the trade dict -- was itself weak)

Each detection includes a `reason` string. This module only reports;
it never changes filters.py's gates or trade_score.py's weights.
"""

from config import (
    REPLAY_FALSE_BREAKOUT_REVERSAL_PCT, REPLAY_LATE_ENTRY_MAX_CANDLES,
    REPLAY_WEAK_MOMENTUM_MAX_CANDLE_RANGE_PCT, ENTRY_SCORE,
)


def _post_entry_candles(candles: list, entry_index: int) -> list:
    if entry_index is None:
        return []
    return candles[entry_index:entry_index + REPLAY_LATE_ENTRY_MAX_CANDLES * 3]


def detect_false_breakout(trade: dict, candles: list) -> dict | None:
    rr = trade.get("replay_result") or {}
    entry = rr.get("entry")
    entry_idx = rr.get("entry_candle_index")
    if entry is None or entry_idx is None:
        return None
    window = _post_entry_candles(candles, entry_idx)
    if not window:
        return None
    lowest = min(c["low"] for c in window)
    reversal_pct = (entry - lowest) / entry if entry else 0.0
    direction = trade.get("direction", "")
    if reversal_pct >= REPLAY_FALSE_BREAKOUT_REVERSAL_PCT and rr.get("outcome") != "WIN":
        label = "FALSE_BREAKDOWN" if direction in ("PE", "SELL") else "FALSE_BREAKOUT"
        return {
            "type": label, "symbol": trade.get("symbol"), "direction": direction,
            "signal_time": trade.get("signal_time"), "reversal_pct": round(reversal_pct * 100, 2),
            "reason": (
                f"Price reversed {reversal_pct * 100:.1f}% against the signal "
                f"direction shortly after entry -- likely a {label.replace('_', ' ').lower()}."
            ),
        }
    return None


def detect_weak_momentum(trade: dict, candles: list) -> dict | None:
    rr = trade.get("replay_result") or {}
    entry = rr.get("entry")
    entry_idx = rr.get("entry_candle_index")
    if entry is None or entry_idx is None or not entry:
        return None
    window = _post_entry_candles(candles, entry_idx)
    if not window:
        return None
    avg_range_pct = sum((c["high"] - c["low"]) / entry for c in window) / len(window)
    if avg_range_pct * 100 < REPLAY_WEAK_MOMENTUM_MAX_CANDLE_RANGE_PCT:
        return {
            "type": "WEAK_MOMENTUM_ENTRY", "symbol": trade.get("symbol"),
            "direction": trade.get("direction"), "signal_time": trade.get("signal_time"),
            "avg_candle_range_pct": round(avg_range_pct * 100, 3),
            "reason": "Little price movement in the candles right after entry -- momentum never followed through.",
        }
    return None


def detect_low_quality_entry(trade: dict) -> dict | None:
    score = trade.get("score")
    outcome = trade.get("outcome")
    if score is not None and outcome == "LOSS" and score < ENTRY_SCORE + 5:
        return {
            "type": "LOW_QUALITY_ENTRY", "symbol": trade.get("symbol"),
            "direction": trade.get("direction"), "signal_time": trade.get("signal_time"),
            "score": score,
            "reason": f"Scored only {score} (barely above the {ENTRY_SCORE} floor) and lost.",
        }
    return None


def detect_poor_option_selection(trade: dict) -> dict | None:
    oi_score = trade.get("options_intelligence", {}).get("score") if isinstance(trade.get("options_intelligence"), dict) else None
    if oi_score is not None and trade.get("outcome") == "LOSS" and oi_score < 50:
        return {
            "type": "POOR_OPTION_SELECTION", "symbol": trade.get("symbol"),
            "direction": trade.get("direction"), "signal_time": trade.get("signal_time"),
            "options_intelligence_score": oi_score,
            "reason": f"Options Intelligence score was only {oi_score} -- chain-level signal (OI/PCR/Greeks/liquidity) was weak despite the alert firing.",
        }
    return None


def detect_false_signals(alerted_trades: list, candles_by_trade: dict) -> list:
    """
    candles_by_trade: {trade_key: candles} -- caller supplies the same
    key it used when building alerted_trades (typically trade["trade_id"]).
    """
    findings = []
    for t in alerted_trades:
        candles = candles_by_trade.get(t.get("trade_id"), [])
        for detector in (detect_false_breakout, detect_weak_momentum):
            finding = detector(t, candles)
            if finding:
                findings.append(finding)
        for detector in (detect_low_quality_entry, detect_poor_option_selection):
            finding = detector(t)
            if finding:
                findings.append(finding)
    return findings
