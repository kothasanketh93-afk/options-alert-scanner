"""Phone push via Firebase Cloud Messaging for the Options Scanner Android app (added 2026-11-21).

Flow: the app registers its FCM token at /api/push-register (Cloudflare Pages Function, behind Cloudflare Access) which stores
{token, email, strategies} in the private R2 bucket as push/devices.json. This module (run by the bot) reads that file, applies each
device's strategy selection and sends one FCM message per device. Everything is best-effort: missing secrets, network errors, a bad
token or an FCM outage never raise and never affect trading/tracking.

Needs env: FCM_SERVICE_ACCOUNT_JSON (Firebase service-account key), R2_ACCOUNT_ID, R2_BUCKET, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY.
R2 is read with a minimal SigV4 GET (stdlib only) so the bot gains no boto3 dependency; only google-auth is needed, for the FCM token.
"""
import datetime
import hashlib
import hmac
import json
import os
import re
import time
import urllib.error
import urllib.request

from logging_setup import get_logger

log = get_logger("fcm")
DEVICES_KEY = "push/devices.json"
DEFAULT_STRATEGIES = ["trade_score", "chase_bypass", "fvg", "ema_pb"]     # same as the dashboard's DEF_ALERT
CHANNEL_ID = "alerts"
_TAG = re.compile(r"<[^>]+>")
_cache = {"devices": (0.0, None), "token": (0.0, None)}


def plain(text: str) -> str:
    import html
    return html.unescape(_TAG.sub("", text or "")).strip()


def base_of(strategy: str) -> str:
    return re.sub(r"_wsl$", "", str(strategy or "").lower())


def wants(device: dict, strategy) -> bool:
    """System/failure alerts (no strategy) go to every device; strategy alerts only to devices that selected that strategy."""
    if not strategy:
        return True
    sel = device.get("strategies")
    return base_of(strategy) in (DEFAULT_STRATEGIES if sel is None else sel)


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def sigv4_headers(method: str, account: str, bucket: str, key: str, access_key: str, secret_key: str, now=None) -> dict:
    """Minimal AWS SigV4 (path-style, empty body) for Cloudflare R2."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    amz, ds = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
    host = f"{account}.r2.cloudflarestorage.com"
    uri = f"/{bucket}/{key}"
    empty = hashlib.sha256(b"").hexdigest()
    canon = (f"{method}\n{uri}\n\nhost:{host}\nx-amz-content-sha256:{empty}\nx-amz-date:{amz}\n\n"
             f"host;x-amz-content-sha256;x-amz-date\n{empty}")
    scope = f"{ds}/auto/s3/aws4_request"
    sts = f"AWS4-HMAC-SHA256\n{amz}\n{scope}\n{hashlib.sha256(canon.encode()).hexdigest()}"
    k = _hmac(("AWS4" + secret_key).encode(), ds)
    for part in ("auto", "s3", "aws4_request"):
        k = _hmac(k, part)
    sig = hmac.new(k, sts.encode(), hashlib.sha256).hexdigest()
    return {"x-amz-date": amz, "x-amz-content-sha256": empty,
            "Authorization": (f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, "
                              f"SignedHeaders=host;x-amz-content-sha256;x-amz-date, Signature={sig}")}


def load_devices(force: bool = False) -> list:
    ts, cached = _cache["devices"]
    if cached is not None and not force and time.time() - ts < 60:
        return cached
    env = os.environ
    need = ("R2_ACCOUNT_ID", "R2_BUCKET", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")
    if not all(env.get(n) for n in need):
        return []
    try:
        hdr = sigv4_headers("GET", env["R2_ACCOUNT_ID"], env["R2_BUCKET"], DEVICES_KEY, env["R2_ACCESS_KEY_ID"], env["R2_SECRET_ACCESS_KEY"])
        req = urllib.request.Request(f"https://{env['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com/{env['R2_BUCKET']}/{DEVICES_KEY}", headers=hdr)
        with urllib.request.urlopen(req, timeout=6) as r:
            devices = json.loads(r.read().decode()).get("devices", [])
    except urllib.error.HTTPError as exc:
        devices = []
        if exc.code != 404:
            log.warning("FCM: reading devices failed (HTTP %s).", exc.code)
    except Exception as exc:
        log.warning("FCM: reading devices failed (%s: %s).", type(exc).__name__, exc)
        devices = []
    _cache["devices"] = (time.time(), devices)
    return devices


def _access_token(info: dict) -> str:
    ts, tok = _cache["token"]
    if tok and time.time() - ts < 2700:
        return tok
    from google.oauth2 import service_account
    from google.auth.transport.requests import Request
    creds = service_account.Credentials.from_service_account_info(info, scopes=["https://www.googleapis.com/auth/firebase.messaging"])
    creds.refresh(Request())
    _cache["token"] = (time.time(), creds.token)
    return creds.token


def send_one(info: dict, token: str, title: str, body: str, high: bool, strategy=None) -> bool:
    msg = {"message": {"token": token,
                       "notification": {"title": title, "body": body},
                       "data": {"strategy": base_of(strategy) if strategy else ""},
                       "android": {"priority": "HIGH" if high else "NORMAL",
                                   "notification": {"channel_id": CHANNEL_ID, "tag": "scanner"}}}}
    url = f"https://fcm.googleapis.com/v1/projects/{info['project_id']}/messages:send"
    try:
        req = urllib.request.Request(url, data=json.dumps(msg).encode(), method="POST",
                                     headers={"Authorization": f"Bearer {_access_token(info)}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=6) as r:
            return 200 <= r.status < 300
    except urllib.error.HTTPError as exc:
        log.warning("FCM send rejected (HTTP %s) for a device token ...%s", exc.code, token[-6:])
        return False
    except Exception as exc:
        log.warning("FCM send failed (%s: %s).", type(exc).__name__, exc)
        return False


def publish(text: str, priority: int = None, strategy: str = None, *, devices=None, sender=None) -> int:
    """Push one notification to every matching registered device. First line = title, rest = body. Returns how many were accepted."""
    raw = os.environ.get("FCM_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw:
        return 0
    try:
        info = json.loads(raw)
        body_all = plain(text)
        if not body_all:
            return 0
        first, _, rest = body_all.partition("\n")
        title = first[:120] or "Options Scanner"
        body = (rest.strip() or first)[:1000]
        high = (priority or 3) >= 4 or any(w in title.lower() for w in ("stop-loss", "sl hit", "failed", "stopped"))
        sent = 0
        for d in (load_devices() if devices is None else devices):
            if d.get("token") and wants(d, strategy):
                ok = (sender or send_one)(info, d["token"], title, body, high, strategy)
                sent += 1 if ok else 0
        log.info("FCM push: %d device(s) (title=%r).", sent, title[:50])
        return sent
    except Exception as exc:                        # never let a push problem touch the run
        log.warning("FCM push failed (%s: %s) -- continuing.", type(exc).__name__, exc)
        return 0
