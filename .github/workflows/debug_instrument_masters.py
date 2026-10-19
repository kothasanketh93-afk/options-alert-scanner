"""
debug_instrument_masters.py
------------------------------------------------------------------------------
ONE-OFF DIAGNOSTIC -- not part of the regular verification flow (that's
verify_stock_symbols.py). Downloads Upstox's and Angel One's public
instrument masters ONCE and dumps raw rows for a handful of known
symbols, so the field-matching logic elsewhere (upstox.py's
resolve_current_month_future(), verify_stock_symbols.py's
check_upstox_stock_options()/check_angelone_equity(), etc.) can be
fixed against ACTUAL current field names/values instead of guessing.

TEMPORARY CHANGE (2026-10-19): the workflow's SECTION input was not
reliably reaching this script's --equity-options-only/--futures-only
flags (two separate runs with different SECTION values produced
byte-identical output), so this version ALWAYS runs BOTH sections
unconditionally, regardless of any flag -- removes the dependency on
that plumbing working correctly for this one-off debug pass. The
--equity-options-only/--futures-only flags are still accepted (so the
command line in the workflow doesn't need to change) but are now
no-ops; nothing is disabled by passing them.

MERGED 2026-07-29: this file used to be two separate scripts run by two
separate workflows --

  1. debug_instrument_masters.py (this file, unchanged section below) --
     EQ + CE/OPTSTK rows for BOTH brokers, 2 symbols (BHEL "known good",
     TATASTEEL "known F&O-broken at the time") -- written to debug
     verify_stock_symbols.py's equity/options matching.
  2. debug_futures_masters.py (folded in below as run_futures_diagnostic())
     -- FUT rows, Upstox ONLY, the original 15-symbol
     InstrumentNotFound failure list -- written to debug upstox.py's
     resolve_current_month_future().

Both scripts downloaded the same public Upstox master independently and
did the same "substring-match rows, dump raw JSON" diagnostic, just for
different instrument_type/symbol sets -- overlapping enough (and both
manual-only, workflow_dispatch-triggered, no secrets) that keeping them
as one file with one shared master download is simpler than two nearly-
identical scripts. EVERY check either original script performed still
runs here, byte-for-byte the same output shape, under its own clearly
labeled section -- nothing was dropped, only the master-download and
row-dump helpers are now shared instead of duplicated.

Usage:
    pip install requests
    python debug_instrument_masters.py             # both diagnostics (always, see note above)
"""
import argparse
import gzip
import json

import requests

UPSTOX_MASTER_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
ANGELONE_MASTER_URL = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"

# --- Section 1 (originally debug_instrument_masters.py): EQ + CE/OPTSTK,
# both brokers. BHEL matched correctly in verify_stock_symbols.py's last
# run, so it's used as the "known good" baseline; TATASTEEL matched on
# equity but not on F&O/options at the time, so it's the "known broken"
# comparison.
EQUITY_OPTIONS_CHECK_SYMBOLS = ["BHEL", "TATASTEEL"]

# --- Section 2 (originally debug_futures_masters.py): FUT rows, Upstox
# only. The exact symbols that failed with InstrumentNotFound in
# resolve_current_month_future() -- updated 2026-10-19 to the symbols
# actually seen failing in the Oct 5-9 production backfill (SYMBOL_FAILED/
# InstrumentNotFound tracebacks), KOTAKBANK leading at 113 occurrences.
FUTURES_CHECK_SYMBOLS = [
    "KOTAKBANK", "TIINDIA", "PHOENIXLTD", "PGEL", "JUBLFOOD", "GMRAIRPORT",
    "FORTIS", "BAJAJ-AUTO", "ANGELONE", "ADANIGREEN", "UPL", "TRENT",
    "HINDPETRO", "POLICYBZR", "DMART", "TITAN", "MPHASIS", "MOTILALOFS",
    "JSWSTEEL", "HDFCBANK", "AMBUJACEM",
    # Original 2026-07-27 failure list, kept for continuity:
    "TATASTEEL", "HINDALCO", "VEDL", "JINDALSTEL", "IDEA", "SAIL",
    "NATIONALUM", "NMDC", "BHEL", "FEDERALBNK", "BANKBARODA", "PFC",
    "RECLTD", "ASHOKLEY", "MOTHERSON",
]
# Known-good symbols for comparison (per upstox.py's docstring, these
# resolve fine via name==symbol).
FUTURES_BASELINE_SYMBOLS = ["NIFTY", "BANKNIFTY"]


def fetch_upstox_master():
    print("Downloading Upstox instrument master...")
    resp = requests.get(UPSTOX_MASTER_URL, timeout=60)
    resp.raise_for_status()
    master = json.loads(gzip.decompress(resp.content))
    print(f"Upstox master: {len(master)} total rows\n")
    return master


def fetch_angelone_master():
    print("Downloading Angel One scrip master...")
    resp = requests.get(ANGELONE_MASTER_URL, timeout=60)
    resp.raise_for_status()
    master = resp.json()
    print(f"Angel One master: {len(master)} total rows\n")
    return master


def _describe_row(row):
    return json.dumps(row, indent=2, default=str)


def run_equity_options_diagnostic(upstox_master, angelone_master):
    """
    Section 1, unchanged from the original debug_instrument_masters.py:
    up to 3 EQ + 3 CE rows per symbol on Upstox, up to 3 EQ + 3 OPTSTK
    rows per symbol on Angel One, substring-matched.
    """
    for symbol in EQUITY_OPTIONS_CHECK_SYMBOLS:
        print("#" * 70)
        print(f"# {symbol}")
        print("#" * 70)

        print(f"\n--- Upstox rows containing '{symbol}' (up to 3 EQ, 3 CE) ---")
        eq_rows = [r for r in upstox_master if r.get("instrument_type") == "EQ" and symbol in json.dumps(r)]
        ce_rows = [r for r in upstox_master if r.get("instrument_type") == "CE" and symbol in json.dumps(r)]
        print(f"Matched EQ rows (substring search): {len(eq_rows)}")
        for r in eq_rows[:3]:
            print(_describe_row(r))
        print(f"Matched CE rows (substring search): {len(ce_rows)}")
        for r in ce_rows[:3]:
            print(_describe_row(r))
        if not ce_rows:
            any_ce = next((r for r in upstox_master if r.get("instrument_type") == "CE"), None)
            print("No CE row matched by substring at all. Sample CE row from file (any symbol):")
            print(_describe_row(any_ce))

        print(f"\n--- Angel One rows containing '{symbol}' (up to 3 EQ, 3 OPTSTK) ---")
        eq_rows_a = [r for r in angelone_master if r.get("instrumenttype") == "EQ" and symbol in json.dumps(r)]
        opt_rows_a = [r for r in angelone_master if r.get("instrumenttype") == "OPTSTK" and symbol in json.dumps(r)]
        print(f"Matched EQ rows (substring search): {len(eq_rows_a)}")
        for r in eq_rows_a[:3]:
            print(_describe_row(r))
        print(f"Matched OPTSTK rows (substring search): {len(opt_rows_a)}")
        for r in opt_rows_a[:3]:
            print(_describe_row(r))
        if not eq_rows_a:
            any_eq_a = next((r for r in angelone_master if r.get("instrumenttype") == "EQ"), None)
            print("No EQ row matched by substring at all. Sample EQ row from file (any symbol):")
            print(_describe_row(any_eq_a))

        print()


def run_futures_diagnostic(upstox_master):
    """
    Section 2, unchanged from the original debug_futures_masters.py:
    NSE_FO instrument_type enumeration, NIFTY/BANKNIFTY FUT baseline,
    then raw any-instrument_type substring rows for every symbol that
    failed InstrumentNotFound in resolve_current_month_future().
    """
    fo_types = sorted({
        row.get("instrument_type")
        for row in upstox_master
        if row.get("segment") == "NSE_FO" or "FO" in str(row.get("segment", ""))
    })
    print(f"Distinct instrument_type values seen under NSE_FO segment: {fo_types}\n")

    print("#" * 70)
    print("# BASELINE (known-working): NIFTY / BANKNIFTY FUT rows")
    print("#" * 70)
    for symbol in FUTURES_BASELINE_SYMBOLS:
        rows = [r for r in upstox_master if r.get("instrument_type") == "FUT" and symbol in json.dumps(r)]
        print(f"\n--- {symbol}: {len(rows)} FUT rows matched (substring search) ---")
        for r in rows[:2]:
            print(_describe_row(r))

    print("\n" + "#" * 70)
    print("# FAILING SYMBOLS: raw rows, any instrument_type, substring match")
    print("#" * 70)
    for symbol in FUTURES_CHECK_SYMBOLS:
        print(f"\n{'=' * 60}\n{symbol}\n{'=' * 60}")

        any_rows = [r for r in upstox_master if symbol in json.dumps(r)]
        deriv_rows = [
            r for r in any_rows
            if r.get("segment") == "NSE_FO"
            or str(r.get("instrument_type", "")) not in ("EQ",)
        ]
        print(f"Total rows containing '{symbol}' (any field, substring): {len(any_rows)}")
        print(f"Of those, non-EQ / NSE_FO rows: {len(deriv_rows)}")
        for r in deriv_rows[:5]:
            print(_describe_row(r))

        if not deriv_rows:
            print(f"!!! NO derivative rows found at all for {symbol} -- "
                  f"this stock may genuinely have no current F&O contract "
                  f"(lost eligibility), rather than a field-name mismatch.")


def main():
    # Flags kept for command-line compatibility with the existing
    # workflow step -- they are now no-ops (see the TEMPORARY CHANGE
    # note in this module's docstring). Both sections always run.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--equity-options-only", action="store_true", help="No-op (see module docstring).")
    parser.add_argument("--futures-only", action="store_true", help="No-op (see module docstring).")
    parser.parse_args()

    upstox_master = fetch_upstox_master()
    angelone_master = fetch_angelone_master()

    print("\n" + "=" * 70)
    print("SECTION 1: Equity + Options rows (Upstox + Angel One)")
    print("=" * 70 + "\n")
    run_equity_options_diagnostic(upstox_master, angelone_master)

    print("\n" + "=" * 70)
    print("SECTION 2: Futures rows (Upstox only)")
    print("=" * 70 + "\n")
    run_futures_diagnostic(upstox_master)


if __name__ == "__main__":
    main()
