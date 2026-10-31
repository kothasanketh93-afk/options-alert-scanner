"""
probe_options_history.py -- ONE-OFF read-only probe (2026-10-31).
Can Upstox (expired-instruments API) or Angel One (SmartAPI) give historical NIFTY option candles
for EXPIRED contracts? Writes a text report only. Never touches the scanner, state or trades.
"""
import os, sys, time
from datetime import datetime, date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import requests  # noqa: E402

OUT = os.environ.get("PROBE_OUT", "probe_options_report.txt")
lines = []


def say(m):
    print(m, flush=True)
    lines.append(str(m))


def upstox():
    say("=== UPSTOX ===")
    try:
        from auth import get_upstox_token
        h = {"Authorization": f"Bearer {get_upstox_token()}", "Accept": "application/json"}
    except Exception as e:
        say(f"token error: {type(e).__name__}: {e}")
        return
    ukey = requests.utils.quote("NSE_INDEX|Nifty 50", safe="")
    r = requests.get(f"https://api.upstox.com/v2/expired-instruments/expiries?instrument_key={ukey}", headers=h, timeout=30)
    say(f"expiries: HTTP {r.status_code} {r.text[:250]}")
    try:
        exps = r.json().get("data", [])
    except Exception:
        exps = []
    say(f"expiries listed={len(exps)} oldest={exps[-1] if exps else '-'} newest={exps[0] if exps else '-'}")
    for exp in [e for e in (exps[-1:] + exps[len(exps)//2:len(exps)//2+1] + exps[:1])]:
        rc = requests.get(f"https://api.upstox.com/v2/expired-instruments/option/contract?instrument_key={ukey}&expiry_date={exp}",
                          headers=h, timeout=30)
        say(f"option contracts {exp}: HTTP {rc.status_code} {rc.text[:200]}")
        try:
            rows = rc.json().get("data", [])
        except Exception:
            rows = []
        say(f"  contracts={len(rows)}")
        if not rows:
            continue
        pick = rows[len(rows)//2]
        ek = pick.get("instrument_key")
        d_to = date.fromisoformat(exp)
        for iv in ("5minute", "1minute", "30minute", "day"):
            time.sleep(0.4)
            url = (f"https://api.upstox.com/v2/expired-instruments/historical-candle/"
                   f"{requests.utils.quote(ek, safe='')}/{iv}/{d_to}/{d_to - timedelta(days=6)}")
            rr = requests.get(url, headers=h, timeout=30)
            try:
                cs = rr.json().get("data", {}).get("candles", [])
            except Exception:
                cs = []
            say(f"  candles {iv} key={ek}: HTTP {rr.status_code} n={len(cs)} {rr.text[:150] if not cs else cs[-1]}")


def angel():
    say("=== ANGEL ONE ===")
    try:
        from angelone import AngelOneBroker, BASE_URL, SCRIP_MASTER_URL
        b = AngelOneBroker()
        b.login()
    except Exception as e:
        say(f"login error: {type(e).__name__}: {e}")
        return
    master = requests.get(SCRIP_MASTER_URL, timeout=90).json()
    opts = [x for x in master if x.get("exch_seg") == "NFO" and x.get("instrumenttype") == "OPTIDX" and x.get("name") == "NIFTY"]
    exps = sorted({datetime.strptime(x["expiry"], "%d%b%Y") for x in opts})
    say(f"NIFTY OPTIDX rows={len(opts)} expiries={len(exps)} first={exps[0].date() if exps else '-'} last={exps[-1].date() if exps else '-'}")
    say(f"expired expiries in master: {sum(1 for e in exps if e.date() < date.today())}")
    if not exps:
        return
    e0 = exps[0]
    cand = [x for x in opts if datetime.strptime(x["expiry"], "%d%b%Y") == e0]
    pick = cand[len(cand)//2]
    for label, back in (("last 5d", 5), ("30d ago", 30), ("90d ago", 90), ("1y ago", 365)):
        end = datetime.now() - timedelta(days=max(0, back - 5)); start = datetime.now() - timedelta(days=back)
        body = {"exchange": "NFO", "symboltoken": pick["token"], "interval": "FIVE_MINUTE",
                "fromdate": start.strftime("%Y-%m-%d 09:00"), "todate": end.strftime("%Y-%m-%d 15:30")}
        time.sleep(0.5)
        r = requests.post(f"{BASE_URL}/rest/secure/angelbroking/historical/v1/getCandleData", json=body, headers=b._headers(), timeout=30)
        try:
            p = r.json()
        except Exception:
            p = {}
        d = p.get("data") or []
        say(f"  {pick['symbol']} {label}: HTTP {r.status_code} status={p.get('status')} n={len(d)} {str(p.get('message'))[:80] if not d else d[0][0]}")


if __name__ == "__main__":
    for f in (upstox, angel):
        try:
            f()
        except Exception as e:
            say(f"{f.__name__} crashed: {type(e).__name__}: {e}")
    open(OUT, "w").write("\n".join(lines) + "\n")
