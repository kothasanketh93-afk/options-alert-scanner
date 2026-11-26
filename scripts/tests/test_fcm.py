import datetime
import importlib
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import fcm_notify  # noqa: E402


def test_wants_defaults_and_selection():
    assert fcm_notify.wants({"strategies": None}, "fvg")
    assert not fcm_notify.wants({"strategies": None}, "orb")
    assert fcm_notify.wants({"strategies": ["orb"]}, "orb_wsl")        # _wsl variant maps to its base strategy
    assert not fcm_notify.wants({"strategies": []}, "fvg")
    assert fcm_notify.wants({"strategies": []}, None)                  # system/failure alerts always go out


def test_publish_filters_devices_and_formats(monkeypatch):
    monkeypatch.setenv("FCM_SERVICE_ACCOUNT_JSON", json.dumps({"project_id": "p"}))
    sent = []
    devs = [{"token": "A" * 30, "strategies": ["fvg"]}, {"token": "B" * 30, "strategies": ["orb"]}, {"strategies": None}]
    n = fcm_notify.publish("<b>New alert · MCX</b>\nGOLD 1 CE · FVG\nEntry 10 · SL 9", strategy="fvg", devices=devs,
                           sender=lambda info, tok, title, body, high, st: sent.append((tok[0], title, body, high, st)) or True)
    assert n == 1 and sent[0][0] == "A"
    assert sent[0][1] == "New alert · MCX" and "Entry 10" in sent[0][2] and sent[0][3] is True   # all alerts high priority (Doze)


def test_stop_loss_is_high_priority_and_system_alert_reaches_all(monkeypatch):
    monkeypatch.setenv("FCM_SERVICE_ACCOUNT_JSON", json.dumps({"project_id": "p"}))
    got = []
    devs = [{"token": "A" * 30, "strategies": ["orb"]}, {"token": "B" * 30, "strategies": []}]
    n = fcm_notify.publish("Stop-loss hit · NSE\nX", devices=devs, sender=lambda *a: got.append(a) or True)
    assert n == 2 and all(g[4] is True for g in got)


def test_no_secret_is_a_noop(monkeypatch):
    monkeypatch.delenv("FCM_SERVICE_ACCOUNT_JSON", raising=False)
    assert fcm_notify.publish("x", devices=[{"token": "T" * 30}], sender=lambda *a: 1 / 0) == 0


def test_sender_errors_never_raise(monkeypatch):
    monkeypatch.setenv("FCM_SERVICE_ACCOUNT_JSON", "not json")
    assert fcm_notify.publish("x\ny", devices=[{"token": "T" * 30}]) == 0


def test_sigv4_matches_botocore():
    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest
    from botocore.credentials import Credentials
    now = datetime.datetime(2026, 11, 21, 3, 4, 5, tzinfo=datetime.timezone.utc)
    mine = fcm_notify.sigv4_headers("GET", "acct123", "bkt", "push/devices.json", "AKID", "SECRET", now=now)
    req2 = AWSRequest(method="GET", url="https://acct123.r2.cloudflarestorage.com/bkt/push/devices.json",
                      headers={"x-amz-content-sha256": mine["x-amz-content-sha256"], "X-Amz-Date": "20261121T030405Z"})
    req2.context["timestamp"] = "20261121T030405Z"
    auth2 = SigV4Auth(Credentials("AKID", "SECRET"), "s3", "auto")
    sig = auth2.signature(auth2.string_to_sign(req2, auth2.canonical_request(req2)), req2)
    assert mine["Authorization"].endswith("Signature=" + sig)


def test_publish_passes_extra_data_and_stays_compatible(monkeypatch):
    import json as _json
    import fcm_notify
    monkeypatch.setenv("FCM_SERVICE_ACCOUNT_JSON", _json.dumps({"project_id": "p"}))
    seen = []
    dev = [{"token": "tok"}]
    fcm_notify.publish("T\nbody", strategy="trade_score", devices=dev,
                       sender=lambda info, tok, title, body, high, strat, extra=None: seen.append(extra) or True,
                       extra={"trade": '{"trade_id":"x"}'})
    fcm_notify.publish("T\nbody", strategy="trade_score", devices=dev,
                       sender=lambda info, tok, title, body, high, strat: seen.append("legacy") or True)
    assert seen == [{"trade": '{"trade_id":"x"}'}, "legacy"]


def test_live_alert_bypasses_eod_only(monkeypatch):
    import config, main, telegram_notify as tg
    sent = []
    monkeypatch.setattr(config, "TELEGRAM_EOD_ONLY", True, raising=False)
    monkeypatch.setattr(config, "TELEGRAM_LIVE_ALERTS", True, raising=False)
    monkeypatch.setattr(tg, "_ALLOW", 0)
    assert tg._suppressed("x") is True                 # outside allow(): still suppressed
    with tg.allow():
        assert tg._suppressed("x") is False            # inside allow(): goes out (what notify_once now does)
