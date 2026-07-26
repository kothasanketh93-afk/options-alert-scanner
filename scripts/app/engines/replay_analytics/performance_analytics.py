"""
app/engines/replay_analytics/performance_analytics.py -- Performance
Analytics Engine.
------------------------------------------------------------------------------
Pure arithmetic over a list of enriched trade records (each already
carrying an "outcome" label from outcome_classifier.classify_outcome()
and a "pnl_pct" from signal_replay/trade_journal). No I/O, no config
thresholds beyond REPLAY_MIN_DRAWDOWN_FOR_RECOVERY_FACTOR -- this
module is deliberately the simplest one in the package so its numbers
are easy to hand-verify.

Expected record shape (superset of what trade_journal.py produces):
    {"outcome": "WIN"|"LOSS"|"BREAKEVEN"|"NO_ENTRY"|"TIME_EXIT"|"INVALID_SIGNAL",
     "pnl_pct": float,             # % move realized, signed
     "max_drawdown_pct": float,    # >= 0
     "holding_minutes": float or None}
"""

from config import REPLAY_MIN_DRAWDOWN_FOR_RECOVERY_FACTOR


def compute_performance(records: list) -> dict:
    tradeable = [r for r in records if r.get("outcome") not in ("NO_ENTRY", "INVALID_SIGNAL")]
    wins = [r for r in tradeable if r["outcome"] == "WIN"]
    losses = [r for r in tradeable if r["outcome"] == "LOSS"]
    breakevens = [r for r in tradeable if r["outcome"] == "BREAKEVEN"]

    total = len(tradeable)
    win_rate = round(len(wins) / total * 100, 2) if total else 0.0
    loss_rate = round(len(losses) / total * 100, 2) if total else 0.0

    avg_profit = round(sum(r["pnl_pct"] for r in wins) / len(wins), 2) if wins else 0.0
    avg_loss = round(sum(r["pnl_pct"] for r in losses) / len(losses), 2) if losses else 0.0  # negative

    gross_profit = sum(r["pnl_pct"] for r in wins)
    gross_loss = abs(sum(r["pnl_pct"] for r in losses))
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss else (float("inf") if gross_profit > 0 else 0.0)

    expectancy = round(sum(r["pnl_pct"] for r in tradeable) / total, 2) if total else 0.0

    holding_times = [r["holding_minutes"] for r in tradeable if r.get("holding_minutes") is not None]
    avg_holding_time = round(sum(holding_times) / len(holding_times), 1) if holding_times else 0.0

    drawdowns = [r.get("max_drawdown_pct", 0.0) for r in tradeable]
    max_drawdown = round(max(drawdowns), 2) if drawdowns else 0.0

    max_consec_wins, max_consec_losses = _max_streaks(tradeable)

    total_return = sum(r["pnl_pct"] for r in tradeable)
    recovery_factor = (
        round(total_return / max_drawdown, 2)
        if max_drawdown >= REPLAY_MIN_DRAWDOWN_FOR_RECOVERY_FACTOR else None
    )

    return {
        "total_trades": total,
        "wins": len(wins), "losses": len(losses), "breakevens": len(breakevens),
        "no_entry": len([r for r in records if r.get("outcome") == "NO_ENTRY"]),
        "invalid_signals": len([r for r in records if r.get("outcome") == "INVALID_SIGNAL"]),
        "win_rate": win_rate, "loss_rate": loss_rate,
        "avg_profit_pct": avg_profit, "avg_loss_pct": avg_loss,
        "profit_factor": profit_factor, "expectancy_pct": expectancy,
        "avg_holding_time_minutes": avg_holding_time,
        "max_drawdown_pct": max_drawdown,
        "max_consecutive_wins": max_consec_wins,
        "max_consecutive_losses": max_consec_losses,
        "recovery_factor": recovery_factor,
    }


def _max_streaks(tradeable: list) -> tuple:
    """Longest consecutive WIN run and longest consecutive LOSS run,
    in chronological order (caller is expected to have already sorted
    `records` by signal time -- this function doesn't re-sort so it
    stays a pure function of the list order given)."""
    max_wins = cur_wins = 0
    max_losses = cur_losses = 0
    for r in tradeable:
        if r["outcome"] == "WIN":
            cur_wins += 1
            cur_losses = 0
        elif r["outcome"] == "LOSS":
            cur_losses += 1
            cur_wins = 0
        else:
            cur_wins = 0
            cur_losses = 0
        max_wins = max(max_wins, cur_wins)
        max_losses = max(max_losses, cur_losses)
    return max_wins, max_losses
