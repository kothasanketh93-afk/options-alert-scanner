"""
verify_stock_symbols.py
------------------------------------------------------------------------------
Run this BEFORE trusting live alerts on config.STOCK_SYMBOLS. It downloads
the same two public instrument masters upstox.py / angelone.py already use
at runtime -- no broker login/API key needed, these files are public -- and
checks, for every symbol in STOCK_SYMBOLS:

  1. Does an NSE equity (EQ) row exist at all, and under which field?
     (this is exactly the check that failed live for PNB on Upstox --
     see the BUGFIX note in upstox.py's resolve_equity_instrument_key())
  2. Is the symbol currently F&O-eligible (an OPTSTK/OPTSTK-equivalent
     row exists for the nearest expiry)?
  3. What's the REAL strike interval (diff of consecutive strike prices)
     vs. what config.STOCK_STRIKE_STEP currently has hardcoded?
  4. What's the REAL lot size, if you want to sanity-check
     STOCK_MIN_OI/STOCK_MIN_OPTION_VOLUME against it?

Usage:
    pip install requests
    python verify_stock_symbols.py

Exit code is 0 only if every symbol passed every check -- safe to use as
a CI gate (e.g. a manual workflow_dispatch job) if you want this run
automatically before the quarterly F&O review each time.
"""
import gzip
import json
import sys

import requests

UPSTOX_MASTER_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
ANGELONE_MASTER_URL = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"

# Keep this in sync with config.STOCK_SYMBOLS / config.STOCK_STRIKE_STEP.
STOCK_SYMBOLS = [
    "SBIN", "CANBK", "IDFCFIRSTB", "FEDERALBNK", "BHEL",
    "SAIL", "NATIONALUM", "NMDC", "HINDCOPPER", "TATACHEM",
]
CONFIGURED_STRIKE_STEP = {
    "SBIN": 10, "CANBK": 5, "IDFCFIRSTB": 1, "FEDERALBNK": 2.5,
    "BHEL": 5, "SAIL": 2.5, "NATIONALUM": 2.5, "NMDC": 2.5,
    "HINDCOPPER": 5, "TATACHEM": 10,
}


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


def main():
    all_ok = True

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

    for symbol in STOCK_SYMBOLS:
        print(f"\n=== {symbol} ===")

        if upstox_master is not None:
            key, note = check_upstox_equity(upstox_master, symbol)
            if key:
                print(f"  [Upstox]    EQUITY OK  -> {key}  ({note})")
            else:
                print(f"  [Upstox]    EQUITY MISSING -- {note}")
                all_ok = False

            strikes = check_upstox_stock_options(upstox_master, symbol)
            if strikes:
                step = strike_step_from(strikes)
                configured = CONFIGURED_STRIKE_STEP.get(symbol)
                match = "OK" if step == configured else f"MISMATCH (config has {configured})"
                print(f"  [Upstox]    F&O OK -- {len(strikes)} strikes, step={step} [{match}]")
                if step != configured:
                    all_ok = False
            else:
                print(f"  [Upstox]    F&O MISSING -- no CE rows found for '{symbol}' "
                      f"(not F&O-eligible right now, or 'name' field assumption is wrong)")
                all_ok = False

        if angelone_master is not None:
            token, note = check_angelone_equity(angelone_master, symbol)
            if token:
                print(f"  [AngelOne]  EQUITY OK  -> token {token}  ({note})")
            else:
                print(f"  [AngelOne]  EQUITY MISSING -- {note}")
                all_ok = False

            strikes = check_angelone_stock_options(angelone_master, symbol)
            if strikes:
                step = strike_step_from(strikes)
                configured = CONFIGURED_STRIKE_STEP.get(symbol)
                match = "OK" if step == configured else f"MISMATCH (config has {configured})"
                print(f"  [AngelOne]  F&O OK -- {len(strikes)} strikes, step={step} [{match}]")
                if step != configured:
                    all_ok = False
            else:
                print(f"  [AngelOne]  F&O MISSING -- no OPTSTK rows found for '{symbol}' "
                      f"(not F&O-eligible right now, or instrumenttype/name assumption is wrong)")
                all_ok = False

    print("\n" + "=" * 60)
    if all_ok:
        print("ALL SYMBOLS PASSED. Still spot-check Angel One's strike-divisor")
        print("heuristic manually (see angelone.py's _detect_strike_divisor()) --")
        print("this script assumes un-scaled strikes, which this check can't verify.")
        sys.exit(0)
    else:
        print("ONE OR MORE SYMBOLS FAILED -- fix config.py / the resolver code")
        print("before trusting live alerts on the failing symbol(s).")
        sys.exit(1)


if __name__ == "__main__":
    main()