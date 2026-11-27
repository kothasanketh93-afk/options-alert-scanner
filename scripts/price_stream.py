"""Live price stream: Upstox Market Data Feed V3 (WebSocket) -> prices.json in private R2 every few seconds (app live strip).

Why Upstox and not the scanner's Angel One: a second Angel login might disturb the scanner's session; a separate broker account
cannot. Uses a read-only 1-year Analytics Token (secret UPSTOX_ANALYTICS_TOKEN) -- no daily manual login, cannot place orders.
Run by .github/workflows/stream.yml. Independent of the scanner; if it dies the app falls back to the scanner's own prices.
Instrument keys are resolved from Upstox's public instrument files (indices fixed, MCX = nearest-expiry future per commodity).
"""
import argparse
import gzip
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

import requests

IST = timezone(timedelta(hours=5, minutes=30))
API = "https://api.upstox.com/v3/feed/market-data-feed/authorize"
INSTR = "https://assets.upstox.com/market-quote/instruments/exchange/{}.json.gz"
INDEX_KEYS = {"NIFTY": "NSE_INDEX|Nifty 50", "BANKNIFTY": "NSE_INDEX|Nifty Bank",
              "MIDCPNIFTY": "NSE_INDEX|NIFTY MID SELECT", "SENSEX": "BSE_INDEX|SENSEX"}
MCX_NAMES = ("CRUDEOIL", "NATURALGAS", "GOLDM", "GOLD", "SILVERM")


def _load(exchange: str) -> list:
    r = requests.get(INSTR.format(exchange), timeout=90)
    r.raise_for_status()
    return json.loads(gzip.decompress(r.content))


def resolve_mcx(rows: list, today_ms: int) -> dict:
    """{name: instrument_key} -- nearest non-expired FUT per commodity (display only; the scanner picks its own contract)."""
    best = {}
    for x in rows:
        if x.get("segment") != "MCX_FO" or x.get("instrument_type") != "FUT":
            continue
        name = x.get("underlying_symbol") or x.get("asset_symbol")
        exp = x.get("expiry") or 0
        if name in MCX_NAMES and exp >= today_ms and (name not in best or exp < best[name][0]):
            best[name] = (exp, x["instrument_key"])
    return {n: v[1] for n, v in best.items()}


def resolve_keys() -> dict:
    keys = dict(INDEX_KEYS)                                 # well-known; verified against the instrument files when reachable
    try:
        nse, bse = _load("NSE"), _load("BSE")
        have = {x["instrument_key"] for x in nse + bse if str(x.get("segment", "")).endswith("_INDEX")}
        for n, k in list(keys.items()):
            if k not in have:
                print(f"index key {k} not in instrument files -- dropping {n}", flush=True)
                del keys[n]
    except Exception as exc:
        print("index key check skipped:", exc, flush=True)
    try:
        today_ms = int(datetime.now(IST).replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
        keys.update(resolve_mcx(_load("MCX"), today_ms))
    except Exception as exc:
        print("MCX keys unavailable:", exc, flush=True)
    return keys


def parse_feeds(msg: dict, by_key: dict) -> dict:
    """Decoded FeedResponse (dict) -> {name: {"ltp","prev","chg"}}. Handles ltpc mode and full-feed shapes."""
    out = {}
    for key, f in ((msg or {}).get("feeds") or {}).items():
        name = by_key.get(key)
        if not name or not isinstance(f, dict):
            continue
        ltpc = f.get("ltpc")
        if not ltpc:
            ff = f.get("fullFeed") or {}
            ltpc = ((ff.get("indexFF") or ff.get("marketFF") or {}).get("ltpc"))
        if not ltpc or ltpc.get("ltp") is None:
            continue
        ltp, prev = float(ltpc["ltp"]), ltpc.get("cp")
        prev = float(prev) if prev else None
        out[name] = {"ltp": round(ltp, 2), "prev": round(prev, 2) if prev else None,
                     "chg": round((ltp - prev) / prev * 100, 2) if prev else None}
    return out


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


def feed_url(token: str) -> str:
    r = requests.get(API, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"}, timeout=15)
    r.raise_for_status()
    return r.json()["data"]["authorized_redirect_uri"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=int, default=350)
    ap.add_argument("--every", type=float, default=4.0)
    args = ap.parse_args()
    token = os.environ.get("UPSTOX_ANALYTICS_TOKEN")
    if not token:
        sys.exit("UPSTOX_ANALYTICS_TOKEN is not set")
    import websocket
    from upstox_client.feeder.proto import MarketDataFeedV3_pb2 as pb
    from google.protobuf import json_format

    keys = resolve_keys()
    by_key = {k: n for n, k in keys.items()}
    print("streaming:", keys, flush=True)
    prices, last_tick, state = {}, [time.time()], {"ws": None}

    def on_open(ws):
        sub = {"guid": uuid.uuid4().hex[:12], "method": "sub", "data": {"mode": "ltpc", "instrumentKeys": list(keys.values())}}
        ws.send(json.dumps(sub).encode(), opcode=websocket.ABNF.OPCODE_BINARY)
        print("subscribed", flush=True)

    def on_message(ws, message):
        try:
            d = json_format.MessageToDict(pb.FeedResponse.FromString(message))
        except Exception:
            return
        got = parse_feeds(d, by_key)
        if got:
            prices.update(got)
            last_tick[0] = time.time()

    def run():
        while True:                                                   # reconnect forever; main loop decides when to stop
            try:
                ws = websocket.WebSocketApp(feed_url(token), on_open=on_open, on_message=on_message,
                                            on_error=lambda w, e: print("ws error:", e, flush=True),
                                            on_close=lambda w, c, m: print("ws closed", c, m, flush=True))
                state["ws"] = ws
                ws.run_forever(ping_interval=20, ping_timeout=10)
            except Exception as exc:
                print("feed connect failed:", exc, flush=True)
            time.sleep(5)

    threading.Thread(target=run, daemon=True).start()
    end = time.time() + args.minutes * 60
    while time.time() < end:
        now = datetime.now(IST)
        if now.hour * 60 + now.minute >= 23 * 60 + 35:
            break
        time.sleep(args.every)
        if prices:
            write_snapshot(dict(prices), "/tmp/prices.json", r2_upload)
    print("stream finished", flush=True)


if __name__ == "__main__":
    main()
