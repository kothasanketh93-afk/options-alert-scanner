import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import pytest
import mcx_signals as ms
from app.engines.sweep_reversal import signals as base


def C(h, m, o, hi, lo, c):
    return {"timestamp": f"2026-11-06T{h:02d}:{m:02d}:00+05:30", "open": o, "high": hi, "low": lo, "close": c, "volume": 1}


def _flat(h0, m0, n, price, spread=0.2):
    out, t = [], h0 * 60 + m0
    for _ in range(n):
        out.append(C(t // 60, t % 60, price, price + spread, price - spread, price))
        t += 5
    return out


@pytest.fixture(autouse=True)
def _late_signals(monkeypatch):
    monkeypatch.setattr(base, "LAST_SIGNAL_MINS", 22 * 60 + 30)


def _orb_range(lo=100.0, hi=101.0):
    return [C(19, 5 * i, 100.5, hi, lo, 100.5) for i in range(6)]


def test_session_orb_long_and_short_and_stale_and_bad_body():
    rng = _orb_range()
    up = rng + [C(19, 30, 100.6, 100.9, 100.2, 100.5), C(19, 35, 101.0, 102.2, 100.9, 102.1)]
    sig = ms.detect_session_orb(up, 1.0)
    assert sig["direction"] == "CE" and sig["extreme"] == 100.5 and sig["level_value"] == 101.0
    dn = rng + [C(19, 30, 100.6, 100.9, 100.2, 100.5), C(19, 35, 100.1, 100.2, 98.9, 99.0)]
    assert ms.detect_session_orb(dn, 1.0)["direction"] == "PE"
    stale = up + [C(19, 40, 102, 102.3, 101.9, 102.2), C(19, 45, 102, 102.3, 101.9, 102.2)]
    assert ms.detect_session_orb(stale, 1.0) is None                       # breakout is no longer fresh
    weak = rng + [C(19, 30, 100.6, 100.9, 100.2, 100.5), C(19, 35, 101.6, 102.6, 100.9, 101.7)]   # close beyond range, body < 50% of range
    assert ms.detect_session_orb(weak, 1.0) is None
    assert ms.detect_session_orb(rng + [C(19, 30, 100.6, 100.9, 100.2, 100.5), C(19, 35, 101.0, 102.2, 100.9, 102.1)], 0.2) is None   # range too wide for ATR


def test_session_sweep_low_reclaim():
    day = _flat(9, 0, 96, 105.0)
    day[10] = C(10, 50, 105, 105.2, 100.0, 105)            # day-session low = 100.0
    eve = _flat(17, 0, 30, 105.0)                         # 17:00 .. 19:25
    cs = day + eve + [C(19, 30, 100.2, 101.0, 99.5, 100.8)]
    sig = ms.detect_session_sweep(cs)
    assert sig["direction"] == "CE" and sig["level_name"] == "DAY_LOW" and sig["extreme"] == 99.5
    assert ms.detect_session_sweep(day + eve) is None      # nothing swept yet


def _trend(n, start=100.0, step=0.1, t0=(9, 0)):
    out, t = [], t0[0] * 60 + t0[1]
    for i in range(n):
        c = start + step * i
        out.append(C(t // 60, t % 60, c - 0.05, c + 0.1, c - 0.1, c))
        t += 5
    return out


def test_ema_pullback_long():
    cs = _trend(106)                                       # 09:00 .. 17:45, steady uptrend
    top = cs[-1]["close"]
    cs[-3] = C(17, 35, top - 0.5, top - 0.4, top - 2.6, top - 0.6)    # dips to the EMA20
    cs[-2] = C(17, 40, top - 0.6, top - 0.3, top - 0.7, top - 0.4)
    cs[-1] = C(17, 45, top - 0.4, top + 0.5, top - 0.5, top + 0.4)    # trigger: bullish, above prior high
    sig = ms.detect_ema_pullback(cs)
    assert sig and sig["direction"] == "CE" and sig["level_name"] == "EMA20" and sig["extreme"] <= top - 2.6


def test_ema_pullback_needs_window_and_trend():
    assert ms.detect_ema_pullback(_trend(50)) is None                       # too few candles
    flat = _flat(9, 0, 110, 100.0)
    assert ms.detect_ema_pullback(flat) is None                             # no trend


def test_news_blackout_windows():
    # Fri 6 Nov 2026 is the first Friday: payrolls 19:00 IST
    assert ms.in_news_blackout("GOLD", 4, 6, 19 * 60) and not ms.in_news_blackout("GOLD", 4, 13, 19 * 60)
    assert ms.in_news_blackout("CRUDEOIL", 2, 4, 21 * 60) and not ms.in_news_blackout("GOLD", 2, 4, 21 * 60)
    assert ms.in_news_blackout("NATURALGAS", 3, 5, 21 * 60 + 20) and not ms.in_news_blackout("NATURALGAS", 3, 5, 22 * 60)
