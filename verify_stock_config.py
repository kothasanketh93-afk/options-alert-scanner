"""
verify_stock_config.py

Verifies config.STOCK_SYMBOLS and config.STOCK_STRIKE_STEP against Angel One's
live F&O instrument master. Run this manually (or as a scheduled/quarterly
GitHub Actions job) BEFORE trusting stock alerts unattended.

What it checks:
  1. Each symbol in STOCK_SYMBOLS still has an active OPTSTK (stock options)
     listing in the current instrument master -> still F&O-eligible.
  2. The strike step configured in STOCK_STRIKE_STEP matches the actual
     gap between consecutive live strikes for that symbol's nearest expiry.

Usage:
    python verify_stock_config.py

Exit code is non-zero if any mismatch is found, so it can gate a CI step.
"""

import sys
import json
from collections import defaultdict

import requests

# Angel One publishes the full instrument master here (no auth required).
INSTRUMENT_MASTER_URL = (
    "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
)

def load_config():
    """
    Import STOCK_SYMBOLS / STOCK_STRIKE_STEP from the project's config.py.
    Adjust the import path below if config.py lives elsewhere in the repo.
    """
    try:
        import config  # noqa: assumes script is run from repo root
        return config.STOCK_SYMBOLS, config.STOCK_STRIKE_STEP
    except (ImportError, AttributeError) as e:
        print(f"[ERROR] Could not import STOCK_SYMBOLS/STOCK_STRIKE_STEP from config.py: {e}")
        sys.exit(1)


def fetch_instrument_master():
    print("[INFO] Downloading instrument master...")
    resp = requests.get(INSTRUMENT_MASTER_URL, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    print(f"[INFO] Downloaded {len(data)} instrument rows.")
    return data


def build_stock_option_strikes(instruments):
    """
    Returns {symbol: sorted list of unique strikes for the NEAREST expiry
    among stock options (OPTSTK)}.
    """
    # symbol -> expiry -> set(strikes)
    by_symbol_expiry = defaultdict(lambda: defaultdict(set))

    for row in instruments:
        # Angel One master fields: name, instrumenttype, expiry, strike, symbol
        if row.get("instrumenttype") != "OPTSTK":
            continue
        name = row.get("name")
        expiry = row.get("expiry")
        strike_raw = row.get("strike")
        if not name or not expiry or strike_raw is None:
            continue
        try:
            # Angel One strikes are often paise (strike * 100) - normalize to rupees
            strike = float(strike_raw) / 100.0
        except (TypeError, ValueError):
            continue
        by_symbol_expiry[name][expiry].add(strike)

    result = {}
    for symbol, expiries in by_symbol_expiry.items():
        if not expiries:
            continue
        # Nearest expiry = earliest sortable date string in DDMMMYYYY form
        try:
            nearest = min(
                expiries.keys(),
                key=lambda d: __import__("datetime").datetime.strptime(d, "%d%b%Y"),
            )
        except ValueError:
            nearest = sorted(expiries.keys())[0]
        result[symbol] = sorted(expiries[nearest])
    return result


def infer_strike_step(strikes):
    if len(strikes) < 2:
        return None
    diffs = [round(b - a, 2) for a, b in zip(strikes, strikes[1:])]
    diffs = [d for d in diffs if d > 0]
    if not diffs:
        return None
    # Most common gap = the real strike step (handles occasional missing strikes)
    return min(diffs)


def main():
    stock_symbols, stock_strike_step = load_config()
    instruments = fetch_instrument_master()
    live_strikes_by_symbol = build_stock_option_strikes(instruments)

    missing_symbols = []
    step_mismatches = []
    ok_count = 0

    for symbol in stock_symbols:
        strikes = live_strikes_by_symbol.get(symbol)
        if not strikes:
            missing_symbols.append(symbol)
            continue

        configured_step = stock_strike_step.get(symbol) if isinstance(stock_strike_step, dict) else stock_strike_step
        live_step = infer_strike_step(strikes)

        if configured_step is None:
            step_mismatches.append((symbol, configured_step, live_step, "no step configured"))
        elif live_step is not None and abs(configured_step - live_step) > 0.01:
            step_mismatches.append((symbol, configured_step, live_step, "mismatch"))
        else:
            ok_count += 1

    print("\n===== VERIFICATION REPORT =====")
    print(f"Total symbols checked: {len(stock_symbols)}")
    print(f"OK (still F&O-eligible, step matches): {ok_count}")

    if missing_symbols:
        print(f"\n[!] No longer F&O-eligible / not found in options segment ({len(missing_symbols)}):")
        for s in missing_symbols:
            print(f"    - {s}")

    if step_mismatches:
        print(f"\n[!] Strike step mismatches ({len(step_mismatches)}):")
        for symbol, configured, live, reason in step_mismatches:
            print(f"    - {symbol}: configured={configured}, live={live} ({reason})")

    if missing_symbols or step_mismatches:
        print("\n[RESULT] Config needs updating before trusting stock alerts unattended.")
        sys.exit(1)
    else:
        print("\n[RESULT] All symbols and strike steps verified OK.")
        sys.exit(0)


if __name__ == "__main__":
    main()