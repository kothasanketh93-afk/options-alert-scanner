"""Validation gates from the build plan, applied per (engine, strategy) to ledger/outcomes.csv.

Gate 1: >=100 trades and >=15 distinct trading days.  Gate 2: time-ordered 60/20/20 split where the
untouched 20% test slice has bootstrap 95% lower bound of mean net_result_pct > 0.
Usage: python3 research/validate_strategies.py path/to/outcomes.csv
Educational research tool, not investment advice.
"""
import csv
import random
import sys
from collections import defaultdict

MIN_TRADES, MIN_DAYS, BOOT = 100, 15, 2000


def boot_lower(xs, seed=7):
    rng = random.Random(seed)
    n = len(xs)
    means = sorted(sum(rng.choice(xs) for _ in range(n)) / n for _ in range(BOOT))
    return means[int(0.025 * BOOT)]


def evaluate(rows):
    rows = sorted(rows, key=lambda r: (r["date"], r["alert_time"]))
    xs = [float(r["net_result_pct"]) for r in rows]
    days = len({r["date"] for r in rows})
    out = {"trades": len(xs), "days": days, "gate1": len(xs) >= MIN_TRADES and days >= MIN_DAYS}
    if not xs:
        return out | {"mean_pct": None, "test_mean_pct": None, "test_lower95": None, "gate2": False}
    a, b = int(len(xs) * 0.6), int(len(xs) * 0.8)
    test = xs[b:]
    out["mean_pct"] = round(sum(xs) / len(xs), 3)
    out["test_mean_pct"] = round(sum(test) / len(test), 3) if test else None
    out["test_lower95"] = round(boot_lower(test), 3) if len(test) >= 10 else None
    out["gate2"] = bool(out["gate1"] and out["test_lower95"] is not None and out["test_lower95"] > 0)
    return out


def main(path):
    groups = defaultdict(list)
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            if r.get("net_result_pct") not in (None, "", "None"):
                groups[(r["engine"], r["strategy"])].append(r)
    print("engine,strategy,trades,days,mean_net_pct,test_mean_pct,test_lower95,gate1,gate2")
    for (eng, strat), rows in sorted(groups.items()):
        o = evaluate(rows)
        print(f"{eng},{strat},{o['trades']},{o['days']},{o['mean_pct']},{o['test_mean_pct']},"
              f"{o['test_lower95']},{o['gate1']},{o['gate2']}")


if __name__ == "__main__":
    main(sys.argv[1])
