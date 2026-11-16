"""One-off research backfill (2026-11-16): the buy-vs-sell comparison (sell_view.py) for ledger trades from BEFORE it existed.
Read-only: never touches state, ledger or trades. Prints one CSV line per trade with prefix 'SV,' (collect from the log).
Only contracts still listed with the broker (not yet expired) can return candles; expired ones are reported as 'nodata'.
Usage: python scripts/backfill_sell_view.py <state-branch dir> <date_from> <date_to> [nse_only]"""
import csv
import gzip
import json
import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import market_data  # noqa: E402
import sell_view  # noqa: E402


def main(root, d0, d1):
    rows = [r for r in csv.DictReader(open(os.path.join(root, "ledger", "outcomes.csv")))
            if d0 <= r["date"] <= d1 and not r.get("sell_result_pct") and r.get("actual_entry_time") and r.get("exit_time")]
    print(f"candidates: {len(rows)}", flush=True)
    masters, cache, ok, miss = {}, {}, 0, 0
    print("SV,date,trade_id,strategy,symbol,direction,expiry,buy_net_pct,sell_pct,status", flush=True)
    for r in rows:
        day = r["date"]
        if day not in masters:
            p = os.path.join(root, "instrument_master", f"{day}.json.gz")
            masters[day] = sell_view.nse_opp_key_fn(json.load(gzip.open(p, "rt")).get("rows", [])) if os.path.exists(p) else None
        fn = masters[day]
        status, pct = "nomaster", ""
        if fn:
            t = {"symbol": r["symbol"], "direction": r["direction"], "strike": r["strike"], "expiry": r["expiry"],
                 "actual_entry_time": r["actual_entry_time"], "final_status": "T2_HIT", "t2_hit_time": r["exit_time"]}
            try:
                key = fn(t)
                since = (sell_view._ts(r["actual_entry_time"]) - timedelta(minutes=3)).isoformat()
                if not key:
                    status = "nokey"
                else:
                    ck = (key, day)
                    if ck not in cache:
                        cache[ck] = market_data.get_1min_candles_since(key, since)
                    res = sell_view.compute(t, key, cache[ck])
                    pct, status = res["pct"], "ok"
                    ok += 1
            except Exception as exc:
                status = f"nodata:{type(exc).__name__}"
                miss += 1
        print(f"SV,{day},{r['trade_id']},{r['strategy']},{r['symbol']},{r['direction']},{r['expiry']},{r['net_result_pct']},{pct},{status}", flush=True)
    print(f"done ok={ok} nodata={miss}", flush=True)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3])
