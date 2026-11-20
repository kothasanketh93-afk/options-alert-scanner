import importlib, json, os, sys
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config, ntfy_notify, telegram_notify as _tg


@pytest.fixture
def tg():
    return importlib.reload(_tg)


class _R:
    status = 200
    def __enter__(self): return self
    def __exit__(self, *a): return False


def test_publish_payload_and_no_topic(monkeypatch):
    monkeypatch.delenv("NTFY_TOPIC", raising=False)
    assert ntfy_notify.publish("x") is False                                   # no topic -> silent no-op
    seen = {}
    monkeypatch.setenv("NTFY_TOPIC", "secret-topic")
    monkeypatch.setattr(ntfy_notify.urllib.request, "urlopen", lambda req, timeout=0: seen.update(j=json.loads(req.data)) or _R())
    assert ntfy_notify.publish("🛑 <b>Stop-loss hit</b>\nNIFTY 25200 PE &amp; more") is True
    j = seen["j"]
    assert j["topic"] == "secret-topic" and j["title"] == "🛑 Stop-loss hit" and "NIFTY 25200 PE & more" in j["message"] and j["priority"] == 4


def test_network_error_is_swallowed(monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "t")
    def boom(*a, **k): raise OSError("down")
    monkeypatch.setattr(ntfy_notify.urllib.request, "urlopen", boom)
    assert ntfy_notify.publish("hello") is False


def test_send_routes_push_and_critical(monkeypatch, tg):
    monkeypatch.setattr(config, "TELEGRAM_EOD_ONLY", True)
    monkeypatch.setattr(tg, "_ALLOW", 0)
    pushed = []
    monkeypatch.setattr(ntfy_notify, "publish", lambda text, priority=None, tags=None, strategy=None: pushed.append((text, priority)) or True)
    tg.request_with_retry = lambda *a, **k: (_ for _ in ()).throw(AssertionError("telegram must not be called for alerts"))
    assert tg.send("trade alert") == {"suppressed": True}
    tg.push("trade alert", "fvg")
    assert tg.send("pre-market shortlist") == {"suppressed": True}              # not pushed -> goes nowhere
    assert pushed == [("trade alert", None)]


def test_topic_per_strategy():
    assert ntfy_notify.topic_for("abc", "Trade Score") == "abc-trade_score"
    assert ntfy_notify.topic_for("abc", None) == "abc"
    assert ntfy_notify.slug("ema_pb_wsl") == "ema_pb_wsl"
