"""
export_options_history.py -- research export (2026-10-31), read-only.
Downloads 5-minute candles (with volume + OI) of EXPIRED NIFTY weekly options from Upstox's
expired-instruments API for strikes near each week's traded range. One CSV.gz per expiry in OUT_DIR.
Never touches the scanner, state or trades.
"""
import csv, gzip, os, sys, time
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import requests  # noqa: E402
from auth import get_upstox_token  # noqa: E402

OUT_DIR = os.environ.get("OUT_DIR", "options_out")
MIN_EXPIRY = os.environ.get("MIN_EXPIRY", "2024-10-24")
MAX_EXPIRIES = int(os.environ.get("MAX_EXPIRIES", "0"))
PAD = 400          # index points beyond the week's high/low
UK = requests.utils.quote("NSE_INDEX|Nifty 50", safe="")
H = {"Authorization": f"Bearer {get_upstox_token()}", "Accept": "application/json"}
reqs = 0


def get(url, tries=5):
    global reqs
    for a in range(tries):
        reqs += 1
        r = requests.get(url, headers=H, timeout=40)
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(2 + 3 * a)
            continue
        return r
    return r


def spot_range(d_from, d_to):
    url = (f"https://api.upstox.com/v3/historical-candle/{UK}/days/1/{d_to.isoformat()}/{d_from.isoformat()}")
    r = get(url)
    cs = r.json().get("data", {}).get("candles", []) if r.status_code == 200 else []
    if not cs:
        return None
    return min(c[3] for c in cs), max(c[2] for c in cs)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    exps = get(f"https://api.upstox.com/v2/expired-instruments/expiries?instrument_key={UK}").json().get("data", [])
    exps = sorted(e for e in exps if e >= MIN_EXPIRY)
    if MAX_EXPIRIES:
        exps = exps[:MAX_EXPIRIES]
    print(f"expiries to export: {len(exps)} ({exps[0]}..{exps[-1]})", flush=True)
    total = 0
    for exp in exps:
        out = os.path.join(OUT_DIR, f"{exp}.csv.gz")
        if os.path.exists(out):
            continue
        d_exp = date.fromisoformat(exp)
        sr = spot_range(d_exp - timedelta(days=8), d_exp)
        rc = get(f"https://api.upstox.com/v2/expired-instruments/option/contract?instrument_key={UK}&expiry_date={exp}")
        rows = rc.json().get("data", []) if rc.status_code == 200 else []
        if not rows or not sr:
            print(f"{exp}: skipped contracts={len(rows)} spot={sr}", flush=True)
            continue
        if total == 0:
            print("sample contract row:", rows[0], flush=True)
        lo, hi = sr[0] - PAD, sr[1] + PAD
        sel = [x for x in rows if x.get("strike_price") is not None and lo <= float(x["strike_price"]) <= hi
               and float(x["strike_price"]) % 50 == 0]
        n = 0
        with gzip.open(out, "wt", newline="") as f:
            w = csv.writer(f)
            w.writerow(["expiry", "strike", "type", "instrument_key", "timestamp", "open", "high", "low", "close", "volume", "oi", "lot_size"])
            for x in sel:
                key = x["instrument_key"]
                typ = x.get("instrument_type") or x.get("option_type") or ""
                url = (f"https://api.upstox.com/v2/expired-instruments/historical-candle/"
                       f"{requests.utils.quote(key, safe='')}/5minute/{d_exp.isoformat()}/{(d_exp - timedelta(days=8)).isoformat()}")
                r = get(url)
                time.sleep(0.08)
                if r.status_code != 200:
                    continue
                for c in r.json().get("data", {}).get("candles", []):
                    w.writerow([exp, x["strike_price"], typ, key] + c[:6] + [c[6] if len(c) > 6 else "", x.get("lot_size")])
                    n += 1
        total += n
        print(f"{exp}: contracts={len(sel)}/{len(rows)} spot_range={sr[0]:.0f}-{sr[1]:.0f} rows={n} reqs={reqs}", flush=True)
    print(f"done rows={total} requests={reqs}", flush=True)


if __name__ == "__main__":
    main()
