import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import importlib, pytest
import config, telegram_notify as _tg


@pytest.fixture(autouse=True)
def tg():
    return importlib.reload(_tg)   # other tests replace tg.send with stubs at module level


def _boom(*a, **k):
    raise AssertionError("network call attempted")


def test_suppressed_unless_allowed(monkeypatch, tg):
    monkeypatch.setattr(config, "TELEGRAM_EOD_ONLY", True)
    monkeypatch.setattr(tg, "request_with_retry", _boom)
    monkeypatch.setattr(tg, "_ALLOW", 0)
    assert tg.send("trade alert") == {"suppressed": True}
    assert tg.send_photo("/nonexistent.png") == {"suppressed": True}

    sent = []
    monkeypatch.setattr(tg, "get_telegram_credentials", lambda: ("t", "c"))
    class R:  # minimal response
        def json(self): return {"ok": True}
    monkeypatch.setattr(tg, "request_with_retry", lambda *a, **k: sent.append(k["data"]["text"]) or R())
    with tg.allow():
        tg.send("EOD report")
    tg.send("state save failed", critical=True)
    assert sent == ["EOD report"]      # critical alerts go to the app only, not Telegram
    assert tg.send("after the block") == {"suppressed": True}      # allow() is scoped


def test_flag_off_restores_old_behaviour(monkeypatch, tg):
    monkeypatch.setattr(config, "TELEGRAM_EOD_ONLY", False)
    monkeypatch.setattr(tg, "_ALLOW", 0)
    monkeypatch.setattr(tg, "get_telegram_credentials", lambda: ("t", "c"))
    got = []
    class R:
        def json(self): return {"ok": True}
    monkeypatch.setattr(tg, "request_with_retry", lambda *a, **k: got.append(1) or R())
    tg.send("anything")
    assert got == [1]
