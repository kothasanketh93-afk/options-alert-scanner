"""Fetch chains (served from the per-run cache when already loaded) and log them. Isolated."""
from pathlib import Path

import config
import market_data
import microstructure_log as mlog
from logging_setup import get_logger
from option_chain import resolve_underlying_key, current_expiry, days_to_expiry

log = get_logger("microstructure")


def collect_once(state, now, ledger_dir: Path) -> int:
    if not getattr(config, "MICROSTRUCTURE_LOG_ENABLED", False) or not mlog.due(state, now):
        return 0
    tmap = mlog.open_trade_map(state)
    symbols = list(config.MICROSTRUCTURE_SYMBOLS)
    for key in ("shadow_trades", "fib_pivot_shadow_trades", "orb_pivot_shadow_trades", "sweep_shadow_trades"):
        for t in (state.get(key) or {}).values():
            if t.get("symbol") and t["symbol"] not in symbols and t.get("instrument_key") in tmap:
                symbols.append(t["symbol"])
    index_symbols = set(symbols)
    if getattr(config, "MICROSTRUCTURE_INCLUDE_SHORTLIST", False):
        for sym in list(getattr(config, "STOCK_SYMBOLS", []) or []):
            if sym not in symbols:
                symbols.append(sym)
    rows = []
    for symbol in symbols:
        try:
            ukey = resolve_underlying_key(symbol)
            if not ukey:
                continue
            expiry = current_expiry(symbol)
            chain = market_data.get_option_chain(ukey, expiry)
            rows += mlog.build_rows(symbol, expiry, days_to_expiry(expiry, now), chain, now,
                                    width=(config.MICROSTRUCTURE_STRIKE_WIDTH if (symbol in index_symbols) else config.MICROSTRUCTURE_STOCK_STRIKE_WIDTH), trade_map=tmap)
        except Exception as exc:
            log.error("MICROSTRUCTURE %s failed (%s: %s)", symbol, type(exc).__name__, exc)
    n = mlog.append_rows(rows, Path(ledger_dir) / f"{now.strftime('%Y-%m-%d')}.csv")
    if rows:
        mlog.mark_done(state, now)
    log.info("MICROSTRUCTURE logged %d row(s) for %s", n, ",".join(symbols))
    return n
