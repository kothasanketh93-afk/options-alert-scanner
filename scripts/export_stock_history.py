"""
export_stock_history.py -- ONE-OFF research export (2026-10-31).
5-minute candles WITH VOLUME for liquid F&O stocks (Upstox v3 historical-candle) + a probe of the
expired stock-option API (expiries / contracts / sample candles). Read-only; never touches the scanner.
"""
import csv, gzip, io, json, os, sys, time
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import requests  # noqa: E402
from auth import get_upstox_token  # noqa: E402

OUT = os.environ.get("EXPORT_DIR", "stock_out")
STOCKS = ["RELIANCE", "HDFCBANK", "ICICIBANK", "SBIN", "INFY", "TCS", "AXISBANK", "KOTAKBANK", "LT", "ITC",
          "BAJFINANCE", "TATASTEEL", "BHARTIARTL", "MARUTI"]
START = date(2024, 9, 2)
H = {"Authorization": f"Bearer {get_upstox_token()}", "Accept": "application/json"}


def get(url):
    for a in range(5):
        r = requests.get(url, headers=H, timeout=40)
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(2 + 3 * a)
            continue
        return r
    return r


def fetch(key, d_from, d_to, depth=0):
    url = f"https://api.upstox.com/v3/historical-candle/{requests.utils.quote(key, safe='')}/minutes/5/{d_to}/{d_from}"
    r = get(url)
    if r.status_code != 200:
        span = (d_to - d_from).days
        if span >= 1 and depth < 6:
            mid = d_from + timedelta(days=span // 2)
            return fetch(key, d_from, mid, depth + 1) + fetch(key, mid + timedelta(days=1), d_to, depth + 1)
        print(f"  ! {d_from}..{d_to} HTTP {r.status_code} {r.text[:100]}", flush=True)
        return []
    return r.json().get("data", {}).get("candles", [])


def months(start, end):
    cur = start
    while cur <= end:
        nxt = (cur.replace(day=1) + timedelta(days=32)).replace(day=1)
        yield cur, min(nxt - timedelta(days=1), end)
        cur = nxt


def main():
    os.makedirs(OUT, exist_ok=True)
    m = requests.get("https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz", timeout=90)
    rows = json.loads(gzip.decompress(m.content))
    eq = {x["trading_symbol"]: x for x in rows if x.get("segment") == "NSE_EQ" and x.get("instrument_type") == "EQ"}
    end = date.today() - timedelta(days=1)
    probe_done = False
    for sym in STOCKS:
        x = eq.get(sym)
        if not x:
            print(f"{sym}: not found in master", flush=True)
            continue
        key = x["instrument_key"]
        if not probe_done:
            uk = requests.utils.quote(key, safe="")
            r = get(f"https://api.upstox.com/v2/expired-instruments/expiries?instrument_key={uk}")
            print(f"PROBE expiries {sym} {key}: HTTP {r.status_code} {r.text[:300]}", flush=True)
            try:
                exps = r.json().get("data", [])
            except Exception:
                exps = []
            print(f"PROBE expiries n={len(exps)} oldest={exps[-1] if exps else '-'} newest={exps[0] if exps else '-'}", flush=True)
            if exps:
                rc = get(f"https://api.upstox.com/v2/expired-instruments/option/contract?instrument_key={uk}&expiry_date={exps[len(exps)//2]}")
                try:
                    cs = rc.json().get("data", [])
                except Exception:
                    cs = []
                print(f"PROBE contracts n={len(cs)} sample={cs[:2]}", flush=True)
            probe_done = True
        data = {}
        for a, b in months(START, end):
            for c in fetch(key, a, b):
                data[c[0]] = c
            time.sleep(0.25)
        path = os.path.join(OUT, f"{sym}_5m.csv.gz")
        with gzip.open(path, "wt", newline="") as f:
            w = csv.writer(f)
            w.writerow(["timestamp", "open", "high", "low", "close", "volume", "oi"])
            for ts in sorted(data):
                w.writerow(data[ts][:7] + [""] * (7 - len(data[ts][:7])))
        print(f"{sym}: {len(data)} candles key={key} lot={x.get('lot_size')}", flush=True)
    json.dump({s: eq[s]["instrument_key"] for s in STOCKS if s in eq}, open(os.path.join(OUT, "keys.json"), "w"))


if __name__ == "__main__":
    main()
