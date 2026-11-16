from datetime import datetime, timedelta, timezone

import config
import mcx_main
import poll_tier
from angelone import BrokerAPIError  # noqa: F401

IST = timezone(timedelta(hours=5, minutes=30))


def T(strategy="fib_pivot", engine="fib_pivot", **kw):
    return {"strategy": strategy, "engine": engine, "status": "ACTIVE", **kw}


def test_shadow_polled_only_after_interval(monkeypatch):
    monkeypatch.setattr(config, "FOCUS_ONLY_NOTICES", True)
    now = datetime(2026, 11, 17, 11, 0, tzinfo=IST)
    t = T()
    assert poll_tier.due(t, now, (15, 5))                      # first poll: always
    poll_tier.mark(t, now)
    assert not poll_tier.due(t, now + timedelta(minutes=5), (15, 5))
    assert not poll_tier.due(t, now + timedelta(minutes=13), (15, 5))
    assert poll_tier.due(t, now + timedelta(minutes=14), (15, 5))


def test_focus_and_live_always_polled(monkeypatch):
    monkeypatch.setattr(config, "FOCUS_ONLY_NOTICES", True)
    now = datetime(2026, 11, 17, 11, 0, tzinfo=IST)
    f = T(strategy="trade_score", engine=None, last_polled_at=now.isoformat())
    assert poll_tier.due(f, now + timedelta(minutes=1), (15, 5))
    s = T(last_polled_at=now.isoformat())
    assert poll_tier.due(s, now + timedelta(minutes=1), (15, 5), live=True)


def test_closing_window_and_off_switch(monkeypatch):
    monkeypatch.setattr(config, "FOCUS_ONLY_NOTICES", True)
    late = datetime(2026, 11, 17, 15, 10, tzinfo=IST)
    t = T(last_polled_at=late.isoformat())
    assert poll_tier.due(t, late + timedelta(minutes=1), (15, 5))
    monkeypatch.setattr(config, "SHADOW_TIERED_POLLING", False)
    early = datetime(2026, 11, 17, 11, 0, tzinfo=IST)
    assert poll_tier.due(T(last_polled_at=early.isoformat()), early + timedelta(minutes=1), (15, 5))


def test_all_trades_fast_when_notices_not_focus_only(monkeypatch):
    monkeypatch.setattr(config, "FOCUS_ONLY_NOTICES", False)
    now = datetime(2026, 11, 17, 11, 0, tzinfo=IST)
    assert poll_tier.due(T(last_polled_at=now.isoformat()), now + timedelta(minutes=1), (15, 5))


def test_mcx_track_open_skips_fresh_shadow_and_fetches_due(monkeypatch):
    monkeypatch.setattr(config, "FOCUS_ONLY_NOTICES", True)
    now = datetime.now(IST)
    mk = lambda strat, polled: {"status": "ACTIVE", "engine": "mcx", "strategy": strat, "instrument_key": f"MCX|{strat}", "last_processed_ts": "2026-11-17T10:00:00+05:30",
                                "last_polled_at": polled}
    st = {"mcx_shadow_trades": {"a": mk("bos", now.isoformat()), "b": mk("ema_pb", now.isoformat()),
                                "c": mk("fvg", (now - timedelta(minutes=20)).isoformat())}}
    calls = []

    class B:
        def get_candles(self, key, interval, since_iso=None):
            calls.append(key)
            return []
    monkeypatch.setattr(mcx_main, "notify_events", lambda *a, **k: None)
    mcx_main.track_open(B(), st)
    assert sorted(calls) == ["MCX|ema_pb", "MCX|fvg"]       # focus (mcx:ema_pb) every run, stale shadow fetched, fresh shadow skipped
