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
    st = {"trades": {"a": _t("A", "ACTIVE")}}
    assert not risk_guard.check(st, "C", _now(10, 0))[0]            # max 1 open trade (paper-phase rule)
    assert not risk_guard.check_global(st, _now(10, 0))[0]
    st = {"trades": {"a": _t("NIFTY", "SL_HIT")}}
    assert risk_guard.check_global(st, _now(10, 0))[0]
    st = {"trades": {"a": _t("NIFTY", "ACTIVE")}}
    assert not risk_guard.check_symbol(st, "NIFTY")[0]
    assert risk_guard.check_symbol(st, "BANKNIFTY")[0]


def test_daily_sl_stop():
    st = {"trades": {str(i): _t("X%d" % i, "SL_HIT") for i in range(3)}}
    assert not risk_guard.check_global(st, _now(10, 0))[0]
    st = {"trades": {"a": _t("X", "SL_HIT")}}
    assert risk_guard.check_global(st, _now(10, 0))[0]


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
    assert risk_guard.rr_check(s)[0]                           # gate is OFF by default (data capture)
    risk_guard.config.RISK_RR_GATE_ENABLED = True
    try:
        assert not risk_guard.rr_check(s)[0]                   # 1.05 < 1.2 when enabled
    finally:
        risk_guard.config.RISK_RR_GATE_ENABLED = False
    risk_guard.config.RISK_RR_GATE_ENABLED = True
    s2 = dict(s, target_1=12.0)
    assert risk_guard.rr_check(s2)[0]
    assert risk_guard.rr_check({"alert_premium": 5.0})[0]       # missing levels: do not block
    risk_guard.config.RISK_RR_GATE_ENABLED = False


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


def test_trades_per_day_cap_and_kill_switch():
    st = {"trades": {"a": _t("A", "T2_HIT"), "b": _t("B", "T2_HIT")}}
    ok, why = risk_guard.check_global(st, _now(10, 0))
    assert not ok and "live alerts today" in why
    risk_guard.config.RISK_KILL_SWITCH = True
    try:
        assert not risk_guard.check_global({"trades": {}}, _now(10, 0))[0]
    finally:
        risk_guard.config.RISK_KILL_SWITCH = False


def test_size_trade_budget_and_enforcement():
    cfg = risk_guard.config
    setup = {"alert_premium": 100.0, "stop_loss": 90.0}
    sz = risk_guard.size_trade(setup, 65)           # (100-90)*65 = 650 + slippage 650*0.01=... + cost 1.5% of 6500
    assert sz["budget_rs"] == 1000 and sz["within_budget"] and sz["lots_allowed"] == 1
    big = risk_guard.size_trade({"alert_premium": 100.0, "stop_loss": 80.0}, 65)    # 1300 + costs > 1000
    assert not big["within_budget"] and big["lots_allowed"] == 0
    assert risk_guard.sizing_check({"alert_premium": 100.0, "stop_loss": 80.0}, 65)[0] is True      # report-only by default
    cfg.RISK_SIZING_ENFORCE = True
    try:
        ok, why, _ = risk_guard.sizing_check({"alert_premium": 100.0, "stop_loss": 80.0}, 65)
        assert not ok and "budget" in why
        assert risk_guard.sizing_check({"alert_premium": 100.0}, 65)[0] is True        # cannot compute -> never blocks on missing data
    finally:
        cfg.RISK_SIZING_ENFORCE = False
