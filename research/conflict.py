"""Conflict study (read-only): when a sweep-family signal fires on a symbol, how do OTHER strategies' signals on the same symbol
within WINDOW minutes AFTER it fare when they point the OPPOSITE way vs the SAME way?

    python research/conflict.py --repo .            # reads origin/state ledgers (NSE + MCX)

Idea (from the 2026-11-19 morning): a sweep CE fired at 09:35 while trend strategies bought PE at 09:40 and all hit SL.
Output is descriptive only. Small samples: treat as a hypothesis for forward data, never as proof. Changes no alert/strategy.
"""
import argparse, csv, io, subprocess
from collections import defaultdict
from datetime import datetime

SWEEP = {"sweep", "sweep_fvg", "sess_sweep"}
ROWS = {"NSE": "ledger/outcomes.csv", "MCX": "mcx/ledger/outcomes.csv"}
WIN = {"T2_HIT", "T1_HIT_THEN_EOD"}


def _t(r):
    return datetime.fromisoformat(r["alert_time"])


def collapse(rows):
    """One row per (date,symbol,strategy,direction,alert minute): drops ITM/ATM/OTM siblings and strategy twins."""
    seen, out = set(), []
    for r in rows:
        k = (r["date"], r["symbol"], r["strategy"].replace("_wsl", ""), r["direction"], r["alert_time"][:16])
        if k not in seen:
            seen.add(k); out.append(r)
    return out


def analyse(rows, window_min=15):
    rows = collapse([r for r in rows if r.get("alert_time")])
    by = defaultdict(list)
    for r in rows:
        by[(r["date"], r["symbol"])].append(r)
    res = {"opposite": [], "same": []}
    pairs = []
    for lst in by.values():
        for s in (x for x in lst if x["strategy"] in SWEEP):
            for o in lst:
                if o is s or o["strategy"] in SWEEP or o["final_status"] == "NOT_ENTERED":
                    continue
                dt = (_t(o) - _t(s)).total_seconds() / 60
                if 0 <= dt <= window_min:
                    res["opposite" if o["direction"] != s["direction"] else "same"].append(o)
                    pairs.append((s, o))
    return res, pairs


def stats(lst):
    n = len(lst)
    if not n:
        return "n=0"
    f = lambda r: float(r.get("net_result_pct") or r.get("result_pct") or 0)
    sl = sum(1 for r in lst if r["final_status"] in ("SL_HIT", "T1_THEN_SL"))
    w = sum(1 for r in lst if r["final_status"] in WIN)
    return f"n={n}  SL%={100*sl/n:.0f}  win%={100*w/n:.0f}  avg_net%={sum(f(r) for r in lst)/n:+.2f}"


def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument("--repo", default="."); ap.add_argument("--window", type=int, default=15)
    a = ap.parse_args(argv)
    for eng, p in ROWS.items():
        r = subprocess.run(["git", "show", "origin/state:" + p], capture_output=True, cwd=a.repo)
        if r.returncode:
            print(f"[{eng}] no ledger"); continue
        res, pairs = analyse(list(csv.DictReader(io.StringIO(r.stdout.decode()))), a.window)
        print(f"\n== {eng}: other-strategy signals within {a.window} min AFTER a sweep signal ==")
        for k in ("opposite", "same"):
            print(f"  {k:9s} direction: {stats(res[k])}")
        print("  distinct sweep signals involved:", len({(s['date'], s['symbol'], s['alert_time'][:16]) for s, _ in pairs}))


if __name__ == "__main__":
    main()
