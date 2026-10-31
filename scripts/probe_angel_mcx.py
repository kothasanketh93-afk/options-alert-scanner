"""
probe_angel_mcx.py -- ONE-OFF read-only feasibility probe (2026-10-31).
Can Angel One SmartAPI give multi-year 5-minute history for MCX gold/crude futures?
Uses the scanner's own AngelOneBroker.login(); writes a text report only.
"""
import os, sys, time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import requests  # noqa: E402
from angelone import AngelOneBroker, BASE_URL, SCRIP_MASTER_URL  # noqa: E402

OUT = os.environ.get("PROBE_OUT", "probe_angel_report.txt")
lines = []


def say(m):
    print(m, flush=True)
    lines.append(str(m))


def main():
    b = AngelOneBroker()
    b.login()
    say("login ok")
    master = requests.get(SCRIP_MASTER_URL, timeout=90).json()
    futs = [x for x in master if x.get("exch_seg") == "MCX" and x.get("instrumenttype") == "FUTCOM"]
    say(f"MCX FUTCOM rows: {len(futs)}")
    now = datetime.now()
    for name in ("GOLD", "GOLDM", "CRUDEOIL", "CRUDEOILM"):
        c = sorted([x for x in futs if x.get("name") == name], key=lambda x: datetime.strptime(x["expiry"], "%d%b%Y"))
        say(f"{name}: {len(c)} live contracts; " + ", ".join(f"{x['symbol']}(tok {x['token']},lot {x.get('lotsize')})" for x in c[:3]))
        if not c:
            continue
        tok = c[0]["token"]
        for label, days_back in (("last 20d", 20), ("~6 months ago", 200), ("~2 years ago", 760), ("~4 years ago", 1500)):
            end = now - timedelta(days=max(0, days_back - 20))
            start = now - timedelta(days=days_back)
            body = {"exchange": "MCX", "symboltoken": tok, "interval": "FIVE_MINUTE",
                    "fromdate": start.strftime("%Y-%m-%d 09:00"), "todate": end.strftime("%Y-%m-%d 23:30")}
            time.sleep(0.5)
            r = requests.post(f"{BASE_URL}/rest/secure/angelbroking/historical/v1/getCandleData",
                              json=body, headers=b._headers(), timeout=30)
            try:
                p = r.json()
            except Exception:
                p = {}
            data = p.get("data") or []
            say(f"  {name} {label}: HTTP {r.status_code} status={p.get('status')} candles={len(data)} "
                f"{(data[0][0] + '..' + data[-1][0]) if data else ''} {'' if p.get('status') else str(p.get('message'))[:100]}")
    open(OUT, "w").write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
