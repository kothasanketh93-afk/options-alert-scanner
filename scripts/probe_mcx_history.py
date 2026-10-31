"""
probe_mcx_history.py -- ONE-OFF read-only feasibility probe (2026-10-31).
Question: can Upstox give multi-year 5-minute history for MCX gold/crude futures?
Writes a text report (PROBE_OUT) only; never touches the scanner, state or trades.
"""
import gzip, io, json, os, sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import requests  # noqa: E402
from auth import get_upstox_token  # noqa: E402

OUT = os.environ.get("PROBE_OUT", "probe_report.txt")
lines = []


def say(msg):
    print(msg, flush=True)
    lines.append(str(msg))


def get(url):
    h = {"Authorization": f"Bearer {get_upstox_token()}", "Accept": "application/json"}
    return requests.get(url, headers=h, timeout=30)


def candles(key, interval_path, d_from, d_to, base="https://api.upstox.com/v3/historical-candle"):
    url = f"{base}/{requests.utils.quote(key, safe='')}/{interval_path}/{d_to}/{d_from}"
    r = get(url)
    if r.status_code != 200:
        return r.status_code, r.text[:160], []
    return 200, "", r.json().get("data", {}).get("candles", [])


def main():
    r = requests.get("https://assets.upstox.com/market-quote/instruments/exchange/MCX.json.gz", timeout=60)
    say(f"MCX master HTTP {r.status_code}")
    rows = json.loads(gzip.decompress(r.content))
    futs = [x for x in rows if x.get("instrument_type") == "FUT" and x.get("segment") == "MCX_FO"]
    say(f"MCX FUT rows: {len(futs)}")
    pick = {}
    for name in ("GOLD", "GOLDM", "CRUDEOIL", "CRUDEOILM"):
        c = sorted([x for x in futs if x.get("name") == name or str(x.get("underlying_symbol")) == name],
                   key=lambda x: x.get("expiry", 0))
        if c:
            pick[name] = c[0]
            say(f"{name}: nearest FUT key={c[0]['instrument_key']} lot={c[0].get('lot_size')} expiry_ms={c[0].get('expiry')} "
                f"underlying_key={c[0].get('underlying_key')}")
        else:
            say(f"{name}: no FUT rows found")
    today = date.today()
    ranges = [("last 30d", today - timedelta(days=30)), ("~6 months ago", today - timedelta(days=200)),
              ("~2 years ago", today - timedelta(days=760)), ("2022", date(2022, 3, 1))]
    for name, x in pick.items():
        key = x["instrument_key"]
        for label, start in ranges:
            end = start + timedelta(days=20)
            if label == "last 30d":
                end = today - timedelta(days=1)
            code, err, cs = candles(key, "minutes/5", start, end)
            span = f"{cs[-1][0]}..{cs[0][0]}" if cs else "-"
            say(f"  live-contract {name} {label}: HTTP {code} candles={len(cs)} {span} {err}")
        # expired-instruments API family: needs the UNDERLYING key, not the contract key
        ukey = x.get("underlying_key")
        q = requests.utils.quote(str(ukey), safe="")
        rr = get(f"https://api.upstox.com/v2/expired-instruments/expiries?instrument_key={q}")
        say(f"  expired-expiries {name} (underlying {ukey}): HTTP {rr.status_code} {rr.text[:300]}")
        try:
            exps = rr.json().get("data", [])
        except Exception:
            exps = []
        say(f"  expiries listed: {len(exps)} oldest={exps[-1] if exps else '-'} newest={exps[0] if exps else '-'}")
        for exp in (exps[-1:] + exps[:1]):
            rc = get(f"https://api.upstox.com/v2/expired-instruments/future/contract?instrument_key={q}&expiry_date={exp}")
            say(f"  expired-contract {name} {exp}: HTTP {rc.status_code} {rc.text[:300]}")
            try:
                ek = rc.json()["data"][0]["instrument_key"]
            except Exception:
                continue
            d_to = date.fromisoformat(exp) - timedelta(days=1)
            code, err, cs = candles(ek, "5minute", d_to - timedelta(days=10), d_to,
                                    base="https://api.upstox.com/v2/expired-instruments/historical-candle")
            say(f"  expired-candles {name} {exp} key={ek}: HTTP {code} candles={len(cs)} {err}")
    open(OUT, "w").write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
