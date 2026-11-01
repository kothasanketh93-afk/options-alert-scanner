"""
export_nifty_extras.py -- ONE-OFF research export (2026-11-01), read-only.
(1) NIFTY futures 5m candles WITH VOLUME (expired monthly contracts + current month) -> used to build VWAP,
    because the NIFTY spot index has no volume.
(2) Sector index 5m candles + India VIX (Upstox v3 historical-candle), for relative-strength research.
"""
import csv, gzip, json, os, sys, time
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import requests  # noqa: E402
from auth import get_upstox_token  # noqa: E402

OUT = os.environ.get("EXPORT_DIR", "extras_out")
START = date(2024, 9, 2)
H = {"Authorization": f"Bearer {get_upstox_token()}", "Accept": "application/json"}
UK = requests.utils.quote("NSE_INDEX|Nifty 50", safe="")
INDEX_NAMES = ["Nifty Bank", "Nifty IT", "Nifty Fin Service", "Nifty Auto", "Nifty FMCG", "Nifty Pharma",
               "Nifty Metal", "Nifty Energy", "India VIX"]


def get(url):
    for a in range(5):
        r = requests.get(url, headers=H, timeout=40)
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(2 + 3 * a)
            continue
        return r
    return r


def v3(key, d_from, d_to, depth=0):
    url = f"https://api.upstox.com/v3/historical-candle/{requests.utils.quote(key, safe='')}/minutes/5/{d_to}/{d_from}"
    r = get(url)
    if r.status_code != 200:
        span = (d_to - d_from).days
        if span >= 1 and depth < 6:
            mid = d_from + timedelta(days=span // 2)
            return v3(key, d_from, mid, depth + 1) + v3(key, mid + timedelta(days=1), d_to, depth + 1)
        print(f"  ! {key} {d_from}..{d_to} HTTP {r.status_code} {r.text[:100]}", flush=True)
        return []
    return r.json().get("data", {}).get("candles", [])


def months(start, end):
    cur = start
    while cur <= end:
        nxt = (cur.replace(day=1) + timedelta(days=32)).replace(day=1)
        yield cur, min(nxt - timedelta(days=1), end)
        cur = nxt


def write(path, data):
    with gzip.open(path, "wt", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "open", "high", "low", "close", "volume", "oi"])
        for ts in sorted(data):
            w.writerow(data[ts][:7] + [""] * (7 - len(data[ts][:7])))


def main():
    os.makedirs(OUT, exist_ok=True)
    today = date.today()
    # ---- futures
    exps = sorted(e for e in get(f"https://api.upstox.com/v2/expired-instruments/expiries?instrument_key={UK}").json().get("data", [])
                  if e >= "2024-09-01")
    fut_rows = {}
    n_ok = 0
    for e in exps:
        rc = get(f"https://api.upstox.com/v2/expired-instruments/future/contract?instrument_key={UK}&expiry_date={e}")
        cs = rc.json().get("data", []) if rc.status_code == 200 else []
        if not cs:
            continue
        key = cs[0]["instrument_key"]
        d_exp = date.fromisoformat(e)
        got = {}
        a = d_exp - timedelta(days=40)
        while a <= d_exp:
            b = min(a + timedelta(days=6), d_exp)
            r = get(f"https://api.upstox.com/v2/expired-instruments/historical-candle/{requests.utils.quote(key, safe='')}/5minute/{b}/{a}")
            if r.status_code == 200:
                for c in r.json().get("data", {}).get("candles", []):
                    got[c[0]] = c
            time.sleep(0.1)
            a = b + timedelta(days=1)
        fut_rows[e] = got
        n_ok += 1
        print(f"FUT {e} key={key} lot={cs[0].get('lot_size')} candles={len(got)}", flush=True)
        with gzip.open(os.path.join(OUT, f"NIFTYFUT_{e}.csv.gz"), "wt", newline="") as f:
            w = csv.writer(f)
            w.writerow(["timestamp", "open", "high", "low", "close", "volume", "oi"])
            for ts in sorted(got):
                w.writerow(got[ts][:7] + [""] * (7 - len(got[ts][:7])))
    # current-month future (still live)
    m = requests.get("https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz", timeout=90)
    rows = json.loads(gzip.decompress(m.content))
    futs = sorted([x for x in rows if x.get("segment") == "NSE_FO" and x.get("instrument_type") == "FUT"
                   and x.get("underlying_symbol") == "NIFTY"], key=lambda x: x["expiry"])
    if futs:
        key = futs[0]["instrument_key"]
        data = {c[0]: c for c in v3(key, date(2026, 9, 25), today - timedelta(days=1))}
        print(f"FUT live {key} candles={len(data)}", flush=True)
        write(os.path.join(OUT, "NIFTYFUT_live.csv.gz"), data)
    # ---- indices
    idx = {x["name"]: x["instrument_key"] for x in rows if x.get("segment") == "NSE_INDEX"}
    print("available index names sample:", sorted(idx)[:60], flush=True)
    for name in INDEX_NAMES:
        key = idx.get(name)
        if not key:
            print(f"{name}: not found", flush=True)
            continue
        data = {}
        for a, b in months(START, today - timedelta(days=1)):
            for c in v3(key, a, b):
                data[c[0]] = c
            time.sleep(0.2)
        write(os.path.join(OUT, f"IDX_{name.replace(' ', '_')}_5m.csv.gz"), data)
        print(f"{name}: {len(data)} candles key={key}", flush=True)


if __name__ == "__main__":
    main()
