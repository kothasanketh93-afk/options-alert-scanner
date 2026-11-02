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


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
    print("ok")
