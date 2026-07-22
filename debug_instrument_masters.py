"""
debug_instrument_masters.py
------------------------------------------------------------------------------
ONE-OFF DIAGNOSTIC -- not part of the regular verification flow. Run this to
see the RAW rows for a couple of well-known, definitely-currently-F&O-active
symbols (BHEL matched correctly in verify_stock_symbols.py's last run, so
it's used as the "known good" baseline; TATASTEEL matched on equity but not
on F&O/options, so it's the "known broken" comparison). Paste the full output
back so verify_stock_symbols.py's check_upstox_stock_options() and
check_angelone_equity() can be fixed against the ACTUAL current field
names/values instead of guessing again.

Usage:
    pip install requests
    python debug_instrument_masters.py
"""
import gzip
import json

import requests

UPSTOX_MASTER_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
ANGELONE_MASTER_URL = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"

CHECK_SYMBOLS = ["BHEL", "TATASTEEL"]  # one that matched, one that didn't


def main():
    print("Downloading Upstox instrument master...")
    resp = requests.get(UPSTOX_MASTER_URL, timeout=60)
    resp.raise_for_status()
    upstox = json.loads(gzip.decompress(resp.content))
    print(f"Upstox master: {len(upstox)} total rows\n")

    print("Downloading Angel One scrip master...")
    resp = requests.get(ANGELONE_MASTER_URL, timeout=60)
    resp.raise_for_status()
    angelone = resp.json()
    print(f"Angel One master: {len(angelone)} total rows\n")

    for symbol in CHECK_SYMBOLS:
        print("#" * 70)
        print(f"# {symbol}")
        print("#" * 70)

        # --- Upstox: one EQ row + one CE row (any expiry) for this symbol ---
        print(f"\n--- Upstox rows containing '{symbol}' (up to 3 EQ, 3 CE) ---")
        eq_rows = [r for r in upstox if r.get("instrument_type") == "EQ" and symbol in json.dumps(r)]
        ce_rows = [r for r in upstox if r.get("instrument_type") == "CE" and symbol in json.dumps(r)]
        print(f"Matched EQ rows (substring search): {len(eq_rows)}")
        for r in eq_rows[:3]:
            print(json.dumps(r, indent=2))
        print(f"Matched CE rows (substring search): {len(ce_rows)}")
        for r in ce_rows[:3]:
            print(json.dumps(r, indent=2))
        if not ce_rows:
            # symbol didn't even appear via substring search in any CE row --
            # show one random CE row from the whole file so we can see what
            # field actually holds the underlying name for options at all.
            any_ce = next((r for r in upstox if r.get("instrument_type") == "CE"), None)
            print("No CE row matched by substring at all. Sample CE row from file (any symbol):")
            print(json.dumps(any_ce, indent=2))

        # --- Angel One: one EQ row + one OPTSTK row for this symbol ---
        print(f"\n--- Angel One rows containing '{symbol}' (up to 3 EQ, 3 OPTSTK) ---")
        eq_rows_a = [r for r in angelone if r.get("instrumenttype") == "EQ" and symbol in json.dumps(r)]
        opt_rows_a = [r for r in angelone if r.get("instrumenttype") == "OPTSTK" and symbol in json.dumps(r)]
        print(f"Matched EQ rows (substring search): {len(eq_rows_a)}")
        for r in eq_rows_a[:3]:
            print(json.dumps(r, indent=2))
        print(f"Matched OPTSTK rows (substring search): {len(opt_rows_a)}")
        for r in opt_rows_a[:3]:
            print(json.dumps(r, indent=2))
        if not eq_rows_a:
            any_eq_a = next((r for r in angelone if r.get("instrumenttype") == "EQ"), None)
            print("No EQ row matched by substring at all. Sample EQ row from file (any symbol):")
            print(json.dumps(any_eq_a, indent=2))

        print()


if __name__ == "__main__":
    main()