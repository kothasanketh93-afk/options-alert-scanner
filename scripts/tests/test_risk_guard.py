"""risk_guard: entry cutoff, max open, one per underlying, daily SL stop."""
import os
import sys
from datetime import datetime

_here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_here))
import risk_guard


def _t(sym, status):
    return {"symbol": sym, "status": status}


def _now(h, m):
    return datetime(2026, 11, 3, h, m)


def test_allows_clean_state():
    assert risk_guard.check({"trades": {}}, "NIFTY", _now(10, 0))[0]


def test_cutoff():
    assert not risk_guard.check({"trades": {}}, "NIFTY", _now(14, 45))[0]
    assert risk_guard.check({"trades": {}}, "NIFTY", _now(14, 44))[0]


def test_max_open_and_same_underlying():
    st = {"trades": {"a": _t("A", "ACTIVE"), "b": _t("B", "WAITING_ENTRY")}}
    assert not risk_guard.check(st, "C", _now(10, 0))[0]
    st = {"trades": {"a": _t("NIFTY", "ACTIVE")}}
    assert not risk_guard.check(st, "NIFTY", _now(10, 0))[0]
    assert risk_guard.check(st, "BANKNIFTY", _now(10, 0))[0]


def test_daily_sl_stop():
    st = {"trades": {str(i): _t("X%d" % i, "SL_HIT") for i in range(4)}}
    assert not risk_guard.check(st, "NIFTY", _now(10, 0))[0]
    st = {"trades": {str(i): _t("X%d" % i, "SL_HIT") for i in range(3)}}
    assert risk_guard.check(st, "NIFTY", _now(10, 0))[0]


def test_disabled_flag():
    risk_guard.config.RISK_GUARD_ENABLED = False
    try:
        assert risk_guard.check({"trades": {}}, "NIFTY", _now(15, 0))[0]
    finally:
        risk_guard.config.RISK_GUARD_ENABLED = True


def test_rr_delhivery_case_and_floor():
    s = {"ask": 9.85, "stop_loss": 8.37, "target_1": 11.4, "target_2": 12.05, "alert_premium": 9.9}
    t1, t2 = risk_guard.reward_risk(s)
    assert (t1, t2) == (1.05, 1.49)
    assert not risk_guard.rr_check(s)[0]                       # 1.05 < 1.2
    s2 = dict(s, target_1=12.0)
    assert risk_guard.rr_check(s2)[0]
    assert risk_guard.rr_check({"alert_premium": 5.0})[0]       # missing levels: do not block


def test_alert_text_shows_rr():
    import telegram_notify as tg
    t = {"ask": 9.85, "bid": 9.75, "spread_pct": 1.02, "entry_high": 10.02,
         "stop_loss": 8.37, "target_1": 11.4, "target_2": 12.05, "alert_premium": 9.9}
    assert "T1 1.05 : 1" in tg._limit_line(t)


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
    test_rr_delhivery_case_and_floor(); test_alert_text_shows_rr(); print("ok")
