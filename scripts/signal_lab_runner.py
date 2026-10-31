"""Fetches candles and runs signal_lab once per day at/after 15:30 IST. Isolated from trading."""
from pathlib import Path

import config
import market_data
import signal_lab
from logging_setup import get_logger
from option_chain import resolve_underlying_key
from app.engines.orb_pivot import signals as orb_signals

log = get_logger("signal_lab")


def _cpr_narrow(daily):
    """Prior-day CPR width < 0.6 x mean of last 10 widths (needs 5 days of history)."""
    ws = []
    for i, d in enumerate(daily[-11:]):
        P = (d["high"] + d["low"] + d["close"]) / 3
        bc = (d["high"] + d["low"]) / 2
        ws.append(abs(P - bc) * 2)
    return len(ws) >= 5 and ws[-1] < 0.6 * (sum(ws) / len(ws))


def collect_once(state: dict, now, ledger_path: Path) -> int:
    if not getattr(config, "SIGNAL_LAB_ENABLED", False):
        return 0
    if (now.hour, now.minute) < (15, 30) or state.get("signal_lab_done"):
        return 0
    rows = []
    for symbol in config.SIGNAL_LAB_SYMBOLS:
        try:
            key = resolve_underlying_key(symbol)
            if not key:
                continue
            daily = market_data.get_recent_daily_candles(key, 12)
            today = market_data.get_recent_underlying_candles(key, "5minute", 80)
            if not daily or len(today) < 8:
                continue
            # daily history may already include today's partial bar -> keep only prior days
            tdate = str(today[0]["timestamp"])[:10]
            daily = [d for d in daily if str(d.get("timestamp", ""))[:10] < tdate] or daily
            prev = (daily[-1]["high"], daily[-1]["low"], daily[-1]["close"])
            try:
                warm = market_data._broker.get_previous_session_candles(key, "5minute", 75) or []
            except Exception:
                warm = []
            rows += signal_lab.compute_rows(
                symbol, warm, today, prev,
                wide_prev_day=orb_signals.is_wide_prev_day(daily),
                narrow_cpr=_cpr_narrow(daily))
        except Exception as exc:
            log.error("SIGNAL_LAB %s failed (%s: %s)", symbol, type(exc).__name__, exc)
    added = signal_lab.append_rows(rows, ledger_path)
    state["signal_lab_done"] = True
    log.info("SIGNAL_LAB appended %d row(s) from %d candidate(s)", added, len(rows))
    return added
