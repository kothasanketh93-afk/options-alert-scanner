"""Live price stream: Angel One SmartAPI WebSocket -> prices.json in private R2 every few seconds (app 'live strip').

Run by .github/workflows/stream.yml during market hours. Independent of the scanner (no state, no alerts); if it dies the
app falls back to the scanner's own 1-5 min prices. Stops by itself at --minutes or 23:35 IST.
NSE/BSE index tokens are fixed; MCX futures tokens come from the scanner's mcx state (`spot[name].token`) via R2.
"""
import argparse
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
# (name, exchangeType, token): 1 = NSE cash/index, 3 = BSE, 5 = MCX
NSE_INDICES = [("NIFTY", 1, "99926000"), ("BANKNIFTY", 1, "99926009"), ("MIDCPNIFTY", 1, "99926074"), ("SENSEX", 3, "99919000")]


def parse_tick(msg, by_key: dict):
    """One WebSocket message -> (name, {"ltp", "prev", "chg"}) or None. Prices arrive in paise."""
    if not isinstance(msg, dict):
        return None
    tok = str(msg.get("token", "")).strip('"')
    name = by_key.get((msg.get("exchange_type"), tok))
    ltp = msg.get("last_traded_price")
    if not name or ltp is None:
        return None
    ltp = ltp / 100.0
    prev = msg.get("closed_price")
    prev = prev / 100.0 if prev else None
    chg = round((ltp - prev) / prev * 100, 2) if prev else None
    return name, {"ltp": round(ltp, 2), "prev": round(prev, 2) if prev else None, "chg": chg}


def write_snapshot(prices: dict, path: str, upload) -> None:
    doc = {"t": datetime.now(IST).isoformat(timespec="seconds"), "p": prices}
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(doc, f)
    os.replace(tmp, path)
    upload(path)


def r2_upload(path: str) -> None:
    acct, bucket = os.environ.get("R2_ACCOUNT_ID"), os.environ.get("R2_BUCKET")
    if not (acct and bucket):
        return
    subprocess.run(["aws", "s3", "cp", path, f"s3://{bucket}/prices.json", "--endpoint-url",
                    f"https://{acct}.r2.cloudflarestorage.com", "--only-show-errors", "--cache-control", "no-store"],
                   check=False, timeout=20)


def mcx_tokens() -> list:
    """[(name, 5, token)] from today's MCX state in R2 (written by the scanner every 5 min)."""
    acct, bucket = os.environ.get("R2_ACCOUNT_ID"), os.environ.get("R2_BUCKET")
    if not (acct and bucket):
        return []
    d = datetime.now(IST).strftime("%Y-%m-%d")
    out = "/tmp/mcx_state.json"
    r = subprocess.run(["aws", "s3", "cp", f"s3://{bucket}/mcx/state/{d}.json", out, "--endpoint-url",
                        f"https://{acct}.r2.cloudflarestorage.com", "--only-show-errors"], check=False, timeout=30)
    if r.returncode != 0:
        return []
    try:
        spot = json.load(open(out)).get("spot") or {}
    except Exception:
        return []
    return [(n, 5, str(v["token"])) for n, v in spot.items() if v.get("token")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=int, default=350)
    ap.add_argument("--every", type=float, default=4.0)
    args = ap.parse_args()
    sys.path.insert(0, os.path.dirname(__file__))
    from SmartApi.smartWebSocketV2 import SmartWebSocketV2
    import angelone
    from auth import get_angelone_credentials

    broker = angelone.AngelOneBroker()
    broker.login()
    creds = get_angelone_credentials()
    items = NSE_INDICES + mcx_tokens()
    by_key = {(et, tok): name for name, et, tok in items}
    groups = {}
    for _n, et, tok in items:
        groups.setdefault(et, []).append(tok)
    token_list = [{"exchangeType": et, "tokens": toks} for et, toks in groups.items()]

    prices, last_tick = {}, [time.time()]
    sws = SmartWebSocketV2(broker._jwt_token, creds["api_key"], creds["client_code"], broker._feed_token)

    def on_open(wsapp):
        sws.subscribe("stream1", 2, token_list)          # mode 2 = QUOTE: ltp + previous close

    def on_data(wsapp, msg):
        r = parse_tick(msg, by_key)
        if r:
            prices[r[0]] = r[1]
            last_tick[0] = time.time()

    sws.on_open, sws.on_data = on_open, on_data
    sws.on_error = lambda w, e=None, *a: print("ws error", e, flush=True)
    sws.on_close = lambda w, *a: print("ws closed", flush=True)
    threading.Thread(target=sws.connect, daemon=True).start()

    end = time.time() + args.minutes * 60
    while time.time() < end:
        now = datetime.now(IST)
        if now.hour * 60 + now.minute >= 23 * 60 + 35:
            break
        time.sleep(args.every)
        if prices:
            write_snapshot(dict(prices), "/tmp/prices.json", r2_upload)
        if time.time() - last_tick[0] > 180 and now.hour * 60 + now.minute < 15 * 60 + 35:
            print("no ticks for 3 min during NSE hours -- exiting so the next job restarts the feed", flush=True)
            sys.exit(1)
    print("stream finished", flush=True)


if __name__ == "__main__":
    main()
