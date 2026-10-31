"""
export_history_angel.py -- ONE-OFF research tool (2026-10-31).
Downloads 5-minute NIFTY / BANKNIFTY index candles from Angel One SmartAPI getCandleData to
cross-check the Upstox-based backtest. Read-only; never touches scanner, state or trades.
Chunked in 25-day windows (adaptive split on errors) with throttling.
"""
import csv, gzip, os, sys, time
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import requests  # noqa: E402
from angelone import AngelOneBroker, BASE_URL  # noqa: E402

INSTR = {"NIFTY": ("NSE", "99926000"), "BANKNIFTY": ("NSE", "99926009")}
START = date(2022, 1, 3)
OUT_DIR = os.environ.get("EXPORT_DIR", "history_out_angel")
broker = None


def fetch(exch, tok, a, b, depth=0):
    body = {"exchange": exch, "symboltoken": tok, "interval": "FIVE_MINUTE",
            "fromdate": a.strftime("%Y-%m-%d 09:00"), "todate": b.strftime("%Y-%m-%d 15:40")}
    for attempt in range(4):
        time.sleep(0.45)
        r = requests.post(f"{BASE_URL}/rest/secure/angelbroking/historical/v1/getCandleData",
                          json=body, headers=broker._headers(), timeout=30)
        try:
            p = r.json()
        except Exception:
            p = {}
        if p.get("status"):
            return p.get("data") or []
        msg = str(p.get("message"))
        if "access rate" in msg.lower() or r.status_code == 429:
            time.sleep(3 + attempt * 3)
            continue
        break
    span = (b - a).days
    if span >= 2 and depth < 5:
        mid = a + timedelta(days=span // 2)
        return fetch(exch, tok, a, mid, depth + 1) + fetch(exch, tok, mid + timedelta(days=1), b, depth + 1)
    print(f"  ! {a}..{b} failed: {r.status_code} {msg[:120]}", flush=True)
    return []


def main():
    global broker
    broker = AngelOneBroker()
    broker.login()
    os.makedirs(OUT_DIR, exist_ok=True)
    end = date.today() - timedelta(days=1)
    for name, (exch, tok) in INSTR.items():
        rows, cur = {}, START
        while cur <= end:
            nxt = min(cur + timedelta(days=24), end)
            got = fetch(exch, tok, cur, nxt)
            for c in got:
                rows[c[0]] = c
            print(f"{name} {cur}..{nxt}: {len(got)}", flush=True)
            cur = nxt + timedelta(days=1)
        with gzip.open(os.path.join(OUT_DIR, f"{name}_5m.csv.gz"), "wt", newline="") as f:
            w = csv.writer(f)
            w.writerow(["timestamp", "open", "high", "low", "close", "volume", "oi"])
            for ts in sorted(rows):
                c = rows[ts]
                w.writerow(list(c[:6]) + [""] * (7 - len(c[:6])))
        print(f"{name}: wrote {len(rows)} candles", flush=True)


if __name__ == "__main__":
    main()
