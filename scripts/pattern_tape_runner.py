"""Fetch candles each run and append new completed-bar rows to the pattern tape. Isolated."""
from pathlib import Path

import config
import market_data
import pattern_tape
import signal_lab
from logging_setup import get_logger
from option_chain import resolve_underlying_key

log = get_logger("pattern_tape")


def collect_once(state, now, ledger_dir: Path) -> int:
    if not getattr(config, "PATTERN_TAPE_ENABLED", False):
        return 0
    mins = now.hour * 60 + now.minute
    if not (9 * 60 + 20 <= mins <= 15 * 60 + 35):
        return 0
    seen = state.setdefault("tape_seen", {})
    day = now.strftime("%Y-%m-%d")
    rows = []
    for symbol in config.PATTERN_TAPE_SYMBOLS:
        try:
            key = resolve_underlying_key(symbol)
            if not key:
                continue
            today = market_data.get_recent_underlying_candles(key, "5minute", 80)
            done = pattern_tape.complete_bars(today, mins)
            if len(done) < 4:
                continue
            bar = str(done[-1]["timestamp"])[11:16]
            if seen.get(symbol) == [day, bar]:
                continue
            daily = market_data.get_recent_daily_candles(key, 12)
            tdate = str(done[0]["timestamp"])[:10]
            daily = [d for d in (daily or []) if str(d.get("timestamp", ""))[:10] < tdate]
            prev = (daily[-1]["high"], daily[-1]["low"], daily[-1]["close"]) if daily else None
            try:
                warm = market_data._broker.get_previous_session_candles(key, "5minute", 75) or []
            except Exception:
                warm = []
            row = pattern_tape.tape_row(symbol, warm, done, prev)
            if row:
                rows.append(row)
                seen[symbol] = [day, bar]
        except Exception as exc:
            log.error("PATTERN_TAPE %s failed (%s: %s)", symbol, type(exc).__name__, exc)
    n = pattern_tape.append_rows(rows, Path(ledger_dir) / f"{day}.csv")
    if n:
        log.info("PATTERN_TAPE logged %d row(s)", n)
    return n
