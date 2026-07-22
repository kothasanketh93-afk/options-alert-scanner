"""
verify_stock_symbols.py
------------------------------------------------------------------------------
Run this BEFORE trusting live alerts on config.STOCK_SYMBOLS. It downloads
the same two public instrument masters upstox.py / angelone.py already use
at runtime -- no broker login/API key needed, these files are public -- and
checks, for every symbol:

  1. Does an NSE equity (EQ) row exist at all, and under which field?
     (this is exactly the check that failed live for PNB on Upstox --
     see the BUGFIX note in upstox.py's resolve_equity_instrument_key())
  2. Is the symbol currently F&O-eligible (an OPTSTK/OPTSTK-equivalent
     row exists for the nearest expiry)?
  3. What's the REAL strike interval (diff of consecutive strike prices)
     vs. what config.STOCK_STRIKE_STEP currently has hardcoded (where a
     configured step exists to compare against)?
  4. What's the REAL lot size, if you want to sanity-check
     STOCK_MIN_OI/STOCK_MIN_OPTION_VOLUME against it?

CHANGED (post Phase-1 opportunity_score.py): config.STOCK_SYMBOLS is no
longer a fixed 15-stock list -- on most days it's populated at runtime
from universe.MASTER_UNIVERSE's top-scored shortlist (~35 of ~100
candidates), with the old 15-stock list (config.FALLBACK_STOCK_WATCHLIST)
only used as the fallback on a day the shortlist cache is missing. This
script now checks BOTH sets instead of a hardcoded copy of the old list,
so a symbol that's F&O-broken in either set gets caught, not just the
fallback names. STOCK_SYMBOLS and STOCK_STRIKE_STEP are imported directly
from config.py rather than duplicated here, so this script can't silently
drift out of sync with config.py the way a hand-copied list can.

Usage:
    pip install requests
    python verify_stock_symbols.py

Exit code is 0 only if every symbol passed every check -- safe to use as
a CI gate (e.g. a manual workflow_dispatch job) if you want this run
automatically before the quarterly F&O review each time.
"""
import gzip
import json
import os
import sys

import requests

# config.py / universe.py live in scripts/, this script lives at repo root.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts"))
import config
import universe

UPSTOX_MASTER_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
ANGELONE_MASTER_URL = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"

# Fallback 15-stock watchlist: gets a full check including strike step,
# since config.STOCK_STRIKE_STEP has a hand-verified value for each of these.
FALLBACK_SYMBOLS = list(config.FALLBACK_STOCK_WATCHLIST)
CONFIGURED_STRIKE_STEP = dict(config.STOCK_STRIKE_STEP)

# The full ~100-stock scoring pool: this is what actually gets scanned most
# days (see config.refresh_symbols_for_today()). No configured strike step
# to compare most of these against -- checked for equity/F&O eligibility
# only, with the live-inferred step printed for information.
UNIVERSE_SYMBOLS = sorted({row["symbol"] for row in universe.MASTER_UNIVERSE} - set(FALLBACK_SYMBOLS))


def fetch_upstox_master():
    print("Downloading Upstox instrument master...")
    resp = requests.get(UPSTOX_MASTER_URL, timeout=60)
    resp.raise_for_status()
    return json.loads(gzip.decompress(resp.content))


def fetch_angelone_master():
    print("Downloading Angel One scrip master...")
    resp = requests.get(ANGELONE_MASTER_URL, timeout=60)
    resp.raise_for_status()
    return resp.json()


def check_upstox_equity(master, symbol):
    matches = [
        row for row in master
        if row.get("instrument_type") == "EQ"
        and (row.get("trading_symbol") == symbol or row.get("name") == symbol)
    ]
    if not matches:
        return None, "NO EQ ROW FOUND (checked trading_symbol and name)"
    field = "trading_symbol" if matches[0].get("trading_symbol") == symbol else "name"
    return matches[0].get("instrument_key"), f"matched via '{field}'"


def check_upstox_stock_options(master, symbol):
    strikes = sorted({
        float(row["strike_price"]) for row in master
        if row.get("name") == symbol and row.get("instrument_type") == "CE"
        and row.get("strike_price")
    })
    return strikes


def check_angelone_equity(master, symbol):
    eq_rows = [r for r in master if r.get("instrumenttype") == "EQ" and r.get("exch_seg") == "NSE"]
    for row in eq_rows:
        if row.get("name") == symbol:
            return row.get("token"), "matched via 'name'"
    for row in eq_rows:
        if row.get("symbol") == f"{symbol}-EQ":
            return row.get("token"), "matched via 'symbol'=='<SYM>-EQ'"
    return None, "NO EQ ROW FOUND (checked name and symbol=='<SYM>-EQ')"


def check_angelone_stock_options(master, symbol):
    strikes = sorted({
        float(row["strike"]) for row in master
        if row.get("name") == symbol and row.get("instrumenttype") == "OPTSTK"
        and row.get("strike")
    })
    return strikes


def strike_step_from(strikes):
    if len(strikes) < 2:
        return None
    diffs = sorted({round(b - a, 4) for a, b in zip(strikes, strikes[1:]) if b > a})
    return diffs[0] if diffs else None


def check_symbol(symbol, upstox_master, angelone_master, check_step: bool) -> list:
    """
    Runs the equity/F&O/(optional) strike-step checks for one symbol
    against both masters. Returns a list of failure-reason strings
    (empty list = symbol fully passed).
    """
    failures = []
    configured = CONFIGURED_STRIKE_STEP.get(symbol) if check_step else None

    if upstox_master is not None:
        key, note = check_upstox_equity(upstox_master, symbol)
        if key:
            print(f"  [Upstox]    EQUITY OK  -> {key}  ({note})")
        else:
            print(f"  [Upstox]    EQUITY MISSING -- {note}")
            failures.append(f"{symbol}: Upstox equity missing")

        strikes = check_upstox_stock_options(upstox_master, symbol)
        if strikes:
            step = strike_step_from(strikes)
            if check_step:
                match = "OK" if step == configured else f"MISMATCH (config has {configured})"
                print(f"  [Upstox]    F&O OK -- {len(strikes)} strikes, step={step} [{match}]")
                if step != configured:
                    failures.append(f"{symbol}: Upstox strike step mismatch (live={step}, config={configured})")
            else:
                print(f"  [Upstox]    F&O OK -- {len(strikes)} strikes, step={step} (no configured step to compare)")
        else:
            print(f"  [Upstox]    F&O MISSING -- no CE rows found for '{symbol}' "
                  f"(not F&O-eligible right now, or 'name' field assumption is wrong)")
            failures.append(f"{symbol}: Upstox F&O missing")

    if angelone_master is not None:
        token, note = check_angelone_equity(angelone_master, symbol)
        if token:
            print(f"  [AngelOne]  EQUITY OK  -> token {token}  ({note})")
        else:
            print(f"  [AngelOne]  EQUITY MISSING -- {note}")
            failures.append(f"{symbol}: AngelOne equity missing")

        strikes = check_angelone_stock_options(angelone_master, symbol)
        if strikes:
            step = strike_step_from(strikes)
            if check_step:
                match = "OK" if step == configured else f"MISMATCH (config has {configured})"
                print(f"  [AngelOne]  F&O OK -- {len(strikes)} strikes, step={step} [{match}]")
                if step != configured:
                    failures.append(f"{symbol}: AngelOne strike step mismatch (live={step}, config={configured})")
            else:
                print(f"  [AngelOne]  F&O OK -- {len(strikes)} strikes, step={step} (no configured step to compare)")
        else:
            print(f"  [AngelOne]  F&O MISSING -- no OPTSTK rows found for '{symbol}' "
                  f"(not F&O-eligible right now, or instrumenttype/name assumption is wrong)")
            failures.append(f"{symbol}: AngelOne F&O missing")

    return failures


def notify_failure(all_failures: list):
    """
    Best-effort Telegram alert on failure -- reuses telegram_notify.send()
    so this shows up the same way other scanner alerts do. Never lets a
    Telegram problem mask the real verification failure: prints a warning
    and still exits 1 either way.
    """
    try:
        import telegram_notify
        lines = "\n".join(f"- {f}" for f in all_failures[:20])
        more = f"\n...and {len(all_failures) - 20} more" if len(all_failures) > 20 else ""
        telegram_notify.send(
            "STOCK CONFIG VERIFICATION FAILED\n\n"
            f"{len(all_failures)} issue(s) found:\n{lines}{more}\n\n"
            "Fix config.py / universe.py before trusting stock alerts unattended."
        )
    except Exception as e:
        print(f"  (Telegram notification also failed: {e})")


def main():
    all_failures = []

    try:
        upstox_master = fetch_upstox_master()
    except Exception as e:
        print(f"  Could not download Upstox master: {e}")
        upstox_master = None

    try:
        angelone_master = fetch_angelone_master()
    except Exception as e:
        print(f"  Could not download Angel One master: {e}")
        angelone_master = None

    print(f"\n{'#' * 60}\n# FALLBACK_STOCK_WATCHLIST ({len(FALLBACK_SYMBOLS)} symbols) -- full check incl. strike step\n{'#' * 60}")
    for symbol in FALLBACK_SYMBOLS:
        print(f"\n=== {symbol} ===")
        all_failures.extend(check_symbol(symbol, upstox_master, angelone_master, check_step=True))

    print(f"\n{'#' * 60}\n# universe.MASTER_UNIVERSE -- remaining {len(UNIVERSE_SYMBOLS)} symbols -- equity/F&O only\n{'#' * 60}")
    for symbol in UNIVERSE_SYMBOLS:
        print(f"\n=== {symbol} ===")
        all_failures.extend(check_symbol(symbol, upstox_master, angelone_master, check_step=False))

    print("\n" + "=" * 60)
    if not all_failures:
        print("ALL SYMBOLS PASSED. Still spot-check Angel One's strike-divisor")
        print("heuristic manually (see angelone.py's _detect_strike_divisor()) --")
        print("this script assumes un-scaled strikes, which this check can't verify.")
        sys.exit(0)
    else:
        print(f"{len(all_failures)} ISSUE(S) FOUND -- fix config.py / universe.py / the resolver code")
        print("before trusting live alerts on the failing symbol(s):")
        for f in all_failures:
            print(f"  - {f}")
        notify_failure(all_failures)
        sys.exit(1)


if __name__ == "__main__":
    main()
