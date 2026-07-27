"""
debug_futures_masters.py
------------------------------------------------------------------------------
ONE-OFF DIAGNOSTIC for the InstrumentNotFound failures on TATASTEEL, HINDALCO,
VEDL, JINDALSTEL, IDEA, SAIL, NATIONALUM, NMDC, BHEL, FEDERALBNK, BANKBARODA,
PFC, RECLTD, ASHOKLEY, MOTHERSON in resolve_current_month_future() (upstox.py).

Meant to be run from a GitHub Actions workflow (network available there),
not locally. Prints raw FUT rows for each symbol so the matching logic in
upstox.py's resolve_current_month_future() can be fixed against the ACTUAL
field values instead of guessing again.

Usage (in CI):
    pip install requests
    python debug_futures_masters.py
"""
import gzip
import json

import requests

UPSTOX_MASTER_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"

# The exact symbols that failed in the 2026-07-27 run.
CHECK_SYMBOLS = [
    "TATASTEEL", "HINDALCO", "VEDL", "JINDALSTEL", "IDEA", "SAIL",
    "NATIONALUM", "NMDC", "BHEL", "FEDERALBNK", "BANKBARODA", "PFC",
    "RECLTD", "ASHOKLEY", "MOTHERSON",
]

# Known-good symbols for comparison (per upstox.py's docstring, these
# resolve fine via name==symbol).
BASELINE_SYMBOLS = ["NIFTY", "BANKNIFTY"]


def describe_row(row):
    return json.dumps(row, indent=2, default=str)


def main():
    print("Downloading Upstox NSE instrument master...")
    resp = requests.get(UPSTOX_MASTER_URL, timeout=60)
    resp.raise_for_status()
    master = json.loads(gzip.decompress(resp.content))
    print(f"Total rows: {len(master)}\n")

    # First: what instrument_type values actually exist for derivative rows?
    fo_types = sorted({
        row.get("instrument_type")
        for row in master
        if row.get("segment") == "NSE_FO" or "FO" in str(row.get("segment", ""))
    })
    print(f"Distinct instrument_type values seen under NSE_FO segment: {fo_types}\n")

    print("#" * 70)
    print("# BASELINE (known-working): NIFTY / BANKNIFTY FUT rows")
    print("#" * 70)
    for symbol in BASELINE_SYMBOLS:
        rows = [r for r in master if r.get("instrument_type") == "FUT" and symbol in json.dumps(r)]
        print(f"\n--- {symbol}: {len(rows)} FUT rows matched (substring search) ---")
        for r in rows[:2]:
            print(describe_row(r))

    print("\n" + "#" * 70)
    print("# FAILING SYMBOLS: raw rows, any instrument_type, substring match")
    print("#" * 70)
    for symbol in CHECK_SYMBOLS:
        print(f"\n{'=' * 60}\n{symbol}\n{'=' * 60}")

        # Any row at all containing this symbol as a substring, regardless
        # of instrument_type -- tells us if the symbol exists in the master
        # under ANY derivative-looking row, and what its real field values
        # look like (name, trading_symbol, instrument_type, segment).
        any_rows = [r for r in master if symbol in json.dumps(r)]
        deriv_rows = [
            r for r in any_rows
            if r.get("segment") == "NSE_FO"
            or str(r.get("instrument_type", "")) not in ("EQ",)
        ]
        print(f"Total rows containing '{symbol}' (any field, substring): {len(any_rows)}")
        print(f"Of those, non-EQ / NSE_FO rows: {len(deriv_rows)}")
        for r in deriv_rows[:5]:
            print(describe_row(r))

        if not deriv_rows:
            print(f"!!! NO derivative rows found at all for {symbol} -- "
                  f"this stock may genuinely have no current F&O contract "
                  f"(lost eligibility), rather than a field-name mismatch.")


if __name__ == "__main__":
    main()
