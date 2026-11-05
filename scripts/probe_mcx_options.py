"""
probe_mcx_options.py -- ONE-OFF read-only feasibility probe (2026-11-05).
Can the scanner trade-shadow MCX commodity OPTIONS (GOLD, GOLDM, CRUDEOIL, NATURALGAS)?
Reports: option contracts/expiries/strikes/lot sizes in the Angel scrip master, underlying futures,
FULL quotes (ltp, bid/ask, OI, volume) for strikes around the money, and 5-minute option candles.
Writes a text report only.
"""
import os, sys, time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import requests  # noqa: E402
from angelone import AngelOneBroker, BASE_URL, SCRIP_MASTER_URL  # noqa: E402

OUT = os.environ.get("PROBE_OUT", "probe_mcx_options_report.txt")
NAMES = ("GOLD", "GOLDM", "CRUDEOIL", "NATURALGAS", "CRUDEOILM", "NATGASMINI", "GOLDPETAL", "GOLDGUINEA")
lines = []


def say(m):
    print(m, flush=True)
    lines.append(str(m))


def exp(x):
    return datetime.strptime(x["expiry"], "%d%b%Y")


def main():
    b = AngelOneBroker()
    b.login()
    say("login ok")
    master = requests.get(SCRIP_MASTER_URL, timeout=(10, 180)).json()
    mcx = [x for x in master if x.get("exch_seg") == "MCX"]
    types = {}
    for x in mcx:
        types[x.get("instrumenttype")] = types.get(x.get("instrumenttype"), 0) + 1
    say(f"MCX instrument types: {types}")
    today = datetime.now()
    for name in NAMES:
        futs = sorted([x for x in mcx if x.get("name") == name and x.get("instrumenttype") == "FUTCOM" and exp(x) >= today.replace(hour=0)], key=exp)
        opts = [x for x in mcx if x.get("name") == name and x.get("instrumenttype") == "OPTFUT" and exp(x) >= today.replace(hour=0)]
        say(f"\n== {name}: live futures={len(futs)} live option rows={len(opts)}")
        for f in futs[:3]:
            say(f"  FUT {f['symbol']} tok={f['token']} lot={f.get('lotsize')} tick={f.get('tick_size')} exp={f['expiry']}")
        if not opts:
            continue
        exps = sorted({exp(x) for x in opts})
        say(f"  option expiries: {[e.strftime('%d%b%Y') for e in exps[:6]]}")
        near = [x for x in opts if exp(x) == exps[0]]
        strikes = sorted({float(x["strike"]) / 100.0 for x in near})
        say(f"  nearest expiry {exps[0].strftime('%d%b%Y')}: {len(near)} rows, strikes {strikes[0]}..{strikes[-1]} (n={len(strikes)}), lot={near[0].get('lotsize')}, tick={near[0].get('tick_size')}")
        say(f"  sample symbols: {[x['symbol'] for x in near[:3]]} raw strike field={near[0]['strike']}")
        if not futs:
            continue
        # underlying future quote
        q = b._batch_quotes("MCX", [futs[0]["token"]]).get(futs[0]["token"], {})
        fut_ltp = q.get("ltp")
        say(f"  FUT ltp={fut_ltp} depth(best)={ (q.get('depth') or {}).get('buy', [{}])[0:1]} / { (q.get('depth') or {}).get('sell', [{}])[0:1]}")
        if not fut_ltp:
            continue
        atm = min(strikes, key=lambda s: abs(s - fut_ltp))
        ring = [s for s in strikes if abs(strikes.index(s) - strikes.index(atm)) <= 2]
        toks = {}
        for x in near:
            if float(x["strike"]) / 100.0 in ring:
                toks[x["token"]] = x
        qs = b._batch_quotes("MCX", list(toks))
        for t, x in sorted(toks.items(), key=lambda kv: (float(kv[1]["strike"]), kv[1]["symbol"])):
            qq = qs.get(t, {})
            d = qq.get("depth") or {}
            bb = (d.get("buy") or [{}])[0].get("price")
            ba = (d.get("sell") or [{}])[0].get("price")
            say(f"    {x['symbol']:<28} ltp={qq.get('ltp')} bid={bb} ask={ba} oi={qq.get('opnInterest')} vol={qq.get('tradeVolume')}")
        # option 5m candle availability
        t0 = next(iter(toks))
        end = datetime.now()
        body = {"exchange": "MCX", "symboltoken": t0, "interval": "FIVE_MINUTE",
                "fromdate": (end - timedelta(days=3)).strftime("%Y-%m-%d 09:00"), "todate": end.strftime("%Y-%m-%d 23:30")}
        time.sleep(0.6)
        r = requests.post(f"{BASE_URL}/rest/secure/angelbroking/historical/v1/getCandleData", json=body, headers=b._headers(), timeout=30)
        try:
            p = r.json()
        except Exception:
            p = {}
        data = p.get("data") or []
        say(f"  option 5m candles (3d) for {toks[t0]['symbol']}: HTTP {r.status_code} status={p.get('status')} n={len(data)} {data[0][0] + '..' + data[-1][0] if data else str(p.get('message'))[:100]}")
    open(OUT, "w").write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
