"""
export_history.py -- ONE-OFF research tool (added 2026-10-31).

Downloads 5-minute index candles from Upstox's v3 historical-candle
endpoint (minute data is available since Jan 2022 per Upstox's own
announcement) so strategy research can be done offline. Reads only; never
touches the scanner, state, or any trade. Run by
.github/workflows/export_history.yml, which commits the CSVs to a
separate `research-data` branch.

Requests are chunked by calendar month; if the API rejects a range
(limit unknown), the chunk is split in half and retried, so this adapts
to whatever per-request limit the endpoint enforces.
"""
import csv
import gzip
import os
import sys
import time
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import requests  # noqa: E402

from auth import get_upstox_token  # noqa: E402

BASE = "https://api.upstox.com/v3"
INSTRUMENTS = {
    "NIFTY": "NSE_INDEX|Nifty 50",
    "BANKNIFTY": "NSE_INDEX|Nifty Bank",
}
START = date(2022, 1, 3)
OUT_DIR = os.environ.get("EXPORT_DIR", "history_out")


def fetch(key: str, d_from: date, d_to: date, depth: int = 0) -> list:
    url = f"{BASE}/historical-candle/{requests.utils.quote(key, safe='')}/minutes/5/{d_to.isoformat()}/{d_from.isoformat()}"
    headers = {"Authorization": f"Bearer {get_upstox_token()}", "Accept": "application/json"}
    for attempt in range(3):
        r = requests.get(url, headers=headers, timeout=30)
        if r.status_code == 429:
            time.sleep(2 + attempt * 3)
            continue
        break
    if r.status_code != 200:
        span = (d_to - d_from).days
        if span >= 1 and depth < 6:
            mid = d_from + timedelta(days=span // 2)
            return fetch(key, d_from, mid, depth + 1) + fetch(key, mid + timedelta(days=1), d_to, depth + 1)
        print(f"  ! {d_from}..{d_to} failed: HTTP {r.status_code} {r.text[:120]}", flush=True)
        return []
    return r.json().get("data", {}).get("candles", [])


def month_ranges(start: date, end: date):
    cur = start
    while cur <= end:
        nxt = (cur.replace(day=1) + timedelta(days=32)).replace(day=1)
        yield cur, min(nxt - timedelta(days=1), end)
        cur = nxt


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    end = date.today() - timedelta(days=1)
    for name, key in INSTRUMENTS.items():
        rows = {}
        for a, b in month_ranges(START, end):
            got = fetch(key, a, b)
            for c in got:
                rows[c[0]] = c
            print(f"{name} {a}..{b}: {len(got)} candles", flush=True)
            time.sleep(0.3)
        path = os.path.join(OUT_DIR, f"{name}_5m.csv.gz")
        with gzip.open(path, "wt", newline="") as f:
            w = csv.writer(f)
            w.writerow(["timestamp", "open", "high", "low", "close", "volume", "oi"])
            for ts in sorted(rows):
                c = rows[ts]
                w.writerow(c[:7] + [""] * (7 - len(c[:7])))
        print(f"{name}: wrote {len(rows)} candles -> {path}", flush=True)


if __name__ == "__main__":
    main()
