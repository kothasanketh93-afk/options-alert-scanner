from datetime import datetime, timedelta, timezone

import ghost_view as g

IST = timezone(timedelta(hours=5, minutes=30))
D = "2026-11-19T"


def T(**kw):
    t = {"trade_id": "x", "status": "NOT_ENTERED", "instrument_key": "K", "alert_time": D + "10:00:00+05:30",
         "alert_premium": 100, "stop_loss": 90, "target_1": 120}
    t.update(kw)
    return t


def C(hhmm, h, l):
    return {"timestamp": D + hhmm + ":00+05:30", "high": h, "low": l, "close": l}


def test_t1_first_is_right_direction():
    r = g.compute(T(), [C("10:01", 105, 98), C("10:05", 121, 100), C("10:10", 100, 80)])
    assert r["result"] == "T1_FIRST" and r["mfe_pct"] == 21.0 and r["mae_pct"] == -20.0


def test_sl_first_and_neither_and_both():
    assert g.compute(T(), [C("10:01", 101, 89), C("10:02", 130, 100)])["result"] == "SL_FIRST"
    assert g.compute(T(), [C("10:01", 110, 95)])["result"] == "NEITHER"
    assert g.compute(T(), [C("10:01", 125, 85)])["result"] == "BOTH"


def test_candles_outside_window_ignored():
    r = g.compute(T(), [C("09:50", 500, 1), C("10:01", 105, 99), C("15:30", 500, 1)])
    assert r["result"] == "NEITHER" and r["mfe_pct"] == 5.0


def test_eligible_only_after_close_and_hold_row():
    t = T()
    assert not g.eligible(t, datetime(2026, 11, 19, 12, 0, tzinfo=IST))
    assert g.hold_row(t, datetime(2026, 11, 19, 12, 0, tzinfo=IST))
    assert g.eligible(t, datetime(2026, 11, 19, 15, 31, tzinfo=IST))
    assert not g.hold_row(t, datetime(2026, 11, 19, 16, 30, tzinfo=IST))   # grace over: write the row anyway
    assert not g.hold_row(T(status="SL_HIT"), datetime(2026, 11, 19, 12, 0, tzinfo=IST))
    mcx = T(market_close_hhmm=[23, 25])
    assert not g.eligible(mcx, datetime(2026, 11, 19, 20, 0, tzinfo=IST))


def test_attach_all_retries_then_gives_up():
    t, now = T(), datetime(2026, 11, 19, 16, 0, tzinfo=IST)

    def boom(k, s):
        raise RuntimeError("api")
    for i in (1, 2, 3):
        assert g.attach_all([t], boom, now) == 0 and t["ghost_view"]["tries"] == i
    assert not g.eligible(t, now) and not g.hold_row(t, now)
    ok = T()
    assert g.attach_all([ok], lambda k, s: [C("10:01", 125, 100)], now) == 1 and ok["ghost_view"]["result"] == "T1_FIRST"
