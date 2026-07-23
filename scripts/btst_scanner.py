"""
btst_scanner.py -- BTST evening entry scan.
----------------------------------------------
Deliberately reuses scanner.py's score_strike() AS-IS for the actual
signal detection (EMA/VWAP/OI/liquidity hard gates + the Trade Score
Engine) rather than re-implementing 250+ lines of proven, already-
bug-fixed detection logic a second time. What's BTST-specific is only:

  1. Which strikes get scanned at all (BTST_SYMBOLS, not SYMBOLS)
  2. An extra quality floor (BTST_MIN_TRADE_SCORE, stricter than intraday)
  3. Minimum days-to-expiry (no same/next-day expiry overnight)
  4. Stock earnings-blackout check (index symbols skip this)
  5. Overnight SL/target levels (btst_levels.py), overriding whatever
     score_strike() computed for the intraday hold

score_strike() itself is called completely unmodified -- this file
imports it, never edits it.
"""

from datetime import datetime, timezone, timedelta

import scanner
import option_chain
from logging_setup import get_logger

import btst_config
import btst_levels
import btst_earnings_guard
import btst_calendar

IST = timezone(timedelta(hours=5, minutes=30))
_logger = get_logger("btst_scanner")


def _in_entry_window(now: datetime) -> bool:
    hhmm = (now.hour, now.minute)
    start, end = btst_config.BTST_ENTRY_WINDOW_HHMM
    return start <= hhmm < end


def scan_candidates(now: datetime = None) -> list:
    """
    Returns a flat list of BTST-qualified candidates (scanner.py's
    score_strike() output dicts, with entry/SL/target overwritten for
    the overnight hold) across BTST_SYMBOLS, best score first. Does
    NOT pick a winner or write state -- btst_main.py does that, same
    separation as scanner.detect_all_setups() / main.py.
    """
    now = now or datetime.now(IST)
    if not _in_entry_window(now):
        _logger.info(f"BTST_SCAN: outside entry window {btst_config.BTST_ENTRY_WINDOW_HHMM} -- skipping")
        return []

    entry_date = now.strftime("%Y-%m-%d")
    exit_date = btst_calendar.next_trading_day(now).strftime("%Y-%m-%d")

    oi_history = {}  # fresh per BTST run -- not shared with the intraday scanner's oi_history
    candidates = []

    for symbol in btst_config.BTST_SYMBOLS:
        is_stock = symbol in btst_config.BTST_STOCK_SYMBOLS

        if is_stock:
            if not btst_earnings_guard.is_configured(symbol):
                _logger.warning(
                    f"BTST_SKIP {symbol}: earnings blackout list has never been "
                    f"reviewed for this symbol (btst_earnings_guard.BLACKOUT_DATES) "
                    f"-- skipping until a human confirms nothing is pending."
                )
                continue
            if btst_earnings_guard.is_blackout(symbol, entry_date, exit_date):
                _logger.info(f"BTST_SKIP {symbol}: earnings/event blackout covers {entry_date}->{exit_date}")
                continue

        try:
            biases = option_chain.directional_bias(symbol)
        except Exception as exc:
            _logger.error(
                f"BTST_SYMBOL_FAILED {symbol}: directional_bias failed ({type(exc).__name__}: {exc})",
                exc_info=True,
            )
            continue
        if not biases:
            continue

        try:
            atm_strike, spot_price = option_chain.get_atm_strike(symbol)
            expiry = option_chain.current_expiry(symbol)
        except Exception as exc:
            _logger.error(
                f"BTST_SYMBOL_FAILED {symbol}: strike/expiry resolution failed ({type(exc).__name__}: {exc})",
                exc_info=True,
            )
            continue

        if option_chain.days_to_expiry(expiry, now) < btst_config.BTST_MIN_DAYS_TO_EXPIRY:
            _logger.info(f"BTST_SKIP {symbol}: expiry {expiry} too close for an overnight hold")
            continue

        for direction in biases:
            buckets = scanner.resolve_strike_candidates(symbol, direction, atm_strike, spot_price, expiry)

            for moneyness, strike_list in buckets.items():
                bucket_setups = []
                for strike in strike_list:
                    try:
                        setup = scanner.score_strike(symbol, direction, moneyness, strike, spot_price, "BTST-EOD", oi_history)
                    except Exception as exc:
                        _logger.error(
                            f"BTST_STRIKE_FAILED {symbol} {direction} {moneyness} {strike}: {type(exc).__name__}: {exc}",
                            exc_info=True,
                        )
                        continue
                    if setup is None:
                        continue
                    if setup["trade_score"] < btst_config.BTST_MIN_TRADE_SCORE:
                        _logger.info(
                            f"BTST_SCORE_TOO_LOW {symbol} {direction} {moneyness} strike={strike} "
                            f"score={setup['trade_score']} (floor={btst_config.BTST_MIN_TRADE_SCORE})"
                        )
                        continue
                    bucket_setups.append(setup)

                if not bucket_setups:
                    continue
                setup = max(bucket_setups, key=lambda s: s["trade_score"])

                levels = btst_levels.overnight_trade_levels(setup["alert_premium"])
                setup.update(levels)
                setup["btst_entry_date"] = entry_date
                setup["btst_exit_date"] = exit_date
                _logger.info(
                    f"BTST_CANDIDATE {symbol} {direction} {moneyness} strike={setup['strike']} "
                    f"score={setup['trade_score']} grade={setup['grade']} "
                    f"entry={setup['alert_premium']} sl={levels['stop_loss']} "
                    f"t1={levels['target_1']} t2={levels['target_2']}"
                )
                candidates.append(setup)

    candidates.sort(key=lambda c: c["trade_score"], reverse=True)
    return candidates
