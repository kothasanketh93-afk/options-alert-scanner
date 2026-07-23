"""
btst_telegram.py
-----------------
Message formatting for BTST lifecycle events. Reuses
telegram_notify.send() for the actual HTTP call (retry/auth/logging
already handled there) -- only the message templates are new.
"""

from telegram_notify import send  # re-used, not duplicated


def _header(p: dict) -> str:
    return f"{p['symbol']} {p['strike']} {p['direction']} (BTST)"


def fmt_new_position(p: dict) -> str:
    return (
        f"BTST — NEW OVERNIGHT POSITION\n\n"
        f"{_header(p)}\n"
        f"Entry Date: {p['btst_entry_date']} | Exit Date: {p['btst_exit_date']}\n"
        f"Expiry: {p['expiry']}\n\n"
        f"Trade Score: {p['trade_score']}/100 | Grade: {p['grade']}\n\n"
        f"Entry Premium: \u20b9{p['alert_premium']}\n"
        f"Stop Loss: \u20b9{p['stop_loss']}\n"
        f"Target 1: \u20b9{p['target_1']}\n"
        f"Target 2: \u20b9{p['target_2']}\n\n"
        f"Status: OPEN (carried overnight)"
    )


def fmt_t1_hit(p: dict) -> str:
    return (
        f"BTST — TARGET 1 HIT\n\n"
        f"{_header(p)}\n"
        f"Entry: \u20b9{p['alert_premium']} | T1: \u20b9{p['target_1']}\n"
        f"Move: {p['max_favorable_pct']:+.2f}%\n\n"
        f"Tracking continues for T2."
    )


def fmt_t2_hit(p: dict) -> str:
    return (
        f"BTST — TARGET 2 HIT\n\n"
        f"{_header(p)}\n"
        f"Entry: \u20b9{p['alert_premium']} | Exit: \u20b9{p['exit_price']}\n"
        f"Move: {p['max_favorable_pct']:+.2f}%\n\n"
        f"Final Result: T2 SUCCESS"
    )


def fmt_sl_hit(p: dict) -> str:
    return (
        f"BTST — STOP LOSS HIT\n\n"
        f"{_header(p)}\n"
        f"Entry: \u20b9{p['alert_premium']} | Exit: \u20b9{p['exit_price']}\n"
        f"Move: {p['max_adverse_pct']:+.2f}%\n\n"
        f"Final Result: {'T1 achieved, SL after T2 attempt' if p['t1_hit'] else 'SL'}"
    )


def fmt_gap_sl_hit(p: dict) -> str:
    return (
        f"BTST — GAP-DOWN, EXIT AT OPEN\n\n"
        f"{_header(p)}\n"
        f"Configured SL: \u20b9{p['stop_loss']} was gapped through overnight -- "
        f"exited at the exit-day's first available price instead.\n"
        f"Entry: \u20b9{p['alert_premium']} | Exit: \u20b9{p['exit_price']}\n\n"
        f"Final Result: GAP SL"
    )


def fmt_force_exit(p: dict) -> str:
    return (
        f"BTST — FORCE EXIT (EOD)\n\n"
        f"{_header(p)}\n"
        f"Entry: \u20b9{p['alert_premium']} | Exit: \u20b9{p['exit_price']}\n"
        f"Neither target nor SL was hit -- closed at "
        f"{btst_force_exit_hhmm_label()} per policy (no second overnight roll).\n\n"
        f"Final Result: {'T1 achieved, then force-exited' if p['t1_hit'] else 'FORCE EXIT'}"
    )


def btst_force_exit_hhmm_label() -> str:
    from btst_config import BTST_FORCE_EXIT_HHMM
    h, m = BTST_FORCE_EXIT_HHMM
    return f"{h:02d}:{m:02d}"


def fmt_ambiguous(p: dict, candle: dict) -> str:
    return (
        f"BTST — AMBIGUOUS CANDLE, MANUAL REVIEW NEEDED\n\n"
        f"{_header(p)}\n"
        f"Both SL and a target were touched in one candle; order can't be "
        f"determined from OHLC.\n\n"
        f"Candle: O {candle['open']} H {candle['high']} L {candle['low']} "
        f"C {candle['close']} @ {candle['timestamp']}\n\n"
        f"SL: \u20b9{p['stop_loss']} | T1: \u20b9{p['target_1']} | T2: \u20b9{p['target_2']}"
    )
