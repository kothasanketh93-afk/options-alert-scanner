"""
export_stock_options.py -- ONE-OFF research export (2026-10-31), read-only.
For each (symbol, date, direction, spot) in research/stock_needs.csv: pick the monthly expiry (first expiry at least
4 calendar days after the date), the ATM and one-strike-ITM contract, and download that day's 5-minute candles
(OHLC, volume, OI) from Upstox's expired-instruments API.
"""
import csv, gzip, json, os, sys, time
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import requests  # noqa: E402
from auth import get_upstox_token  # noqa: E402

OUT = os.environ.get("OUT_FILE", "stock_options.csv.gz")
NEEDS = os.environ.get("NEEDS", "research/stock_needs.csv")
H = {"Authorization": f"Bearer {get_upstox_token()}", "Accept": "application/json"}
reqs = 0


def get(url):
    global reqs
    for a in range(5):
        reqs += 1
        r = requests.get(url, headers=H, timeout=40)
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(2 + 3 * a)
            continue
        return r
    return r


def main():
    m = requests.get("https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz", timeout=90)
    eq = {x["trading_symbol"]: x["instrument_key"] for x in json.loads(gzip.decompress(m.content))
          if x.get("segment") == "NSE_EQ" and x.get("instrument_type") == "EQ"}
    needs = list(csv.DictReader(open(NEEDS)))
    exps, contracts = {}, {}
    w = gzip.open(OUT, "wt", newline="")
    cw = csv.writer(w)
    cw.writerow(["symbol", "date", "direction", "sel", "expiry", "strike", "lot_size", "instrument_key", "timestamp",
                 "open", "high", "low", "close", "volume", "oi"])
    done = 0
    for n in needs:
        sym, d, dr, spot = n["symbol"], n["date"], n["direction"], float(n["spot"])
        uk = requests.utils.quote(eq[sym], safe="")
        if sym not in exps:
            exps[sym] = sorted(get(f"https://api.upstox.com/v2/expired-instruments/expiries?instrument_key={uk}").json().get("data", []))
        dd = date.fromisoformat(d)
        exp = next((e for e in exps[sym] if (date.fromisoformat(e) - dd).days >= 4), None)
        if not exp:
            continue
        if (sym, exp) not in contracts:
            rr = get(f"https://api.upstox.com/v2/expired-instruments/option/contract?instrument_key={uk}&expiry_date={exp}")
            contracts[(sym, exp)] = rr.json().get("data", []) if rr.status_code == 200 else []
        rows = [x for x in contracts[(sym, exp)] if x.get("instrument_type") == dr and x.get("strike_price") is not None]
        if not rows:
            continue
        strikes = sorted({float(x["strike_price"]) for x in rows})
        atm = min(strikes, key=lambda s: abs(s - spot))
        i = strikes.index(atm)
        itm = strikes[i - 1] if dr == "CE" and i > 0 else (strikes[i + 1] if dr == "PE" and i + 1 < len(strikes) else None)
        for sel, st in (("ATM", atm), ("ITM1", itm)):
            if st is None:
                continue
            x = next(r for r in rows if float(r["strike_price"]) == st)
            url = (f"https://api.upstox.com/v2/expired-instruments/historical-candle/"
                   f"{requests.utils.quote(x['instrument_key'], safe='')}/5minute/{d}/{d}")
            r = get(url)
            time.sleep(0.08)
            if r.status_code != 200:
                continue
            for c in r.json().get("data", {}).get("candles", []):
                cw.writerow([sym, d, dr, sel, exp, st, x.get("lot_size"), x["instrument_key"]] + c[:6] + [c[6] if len(c) > 6 else ""])
        done += 1
        if done % 100 == 0:
            print(f"{done}/{len(needs)} needs, reqs={reqs}", flush=True)
    w.close()
    print(f"done needs={done} reqs={reqs}", flush=True)


if __name__ == "__main__":
    main()
