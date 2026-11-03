import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import eod_report


def _t(sym, entry, sl, t2, status, at="2026-11-03T09:31:00"):
    return {"symbol": sym, "direction": "CE", "strike": 100, "alert_time": at, "tracked_entry_price": entry,
            "stop_loss": sl, "target_2": t2, "final_status": status}


def test_pnl_and_live_twin_not_double_counted():
    live = _t("AAA", 10.0, 8.0, 14.0, "SL_HIT")
    st = {"trades": {"a": live}, "shadow_trades": {"b": dict(live), "c": _t("BBB", 20.0, 18.0, 25.0, "T2_HIT")}}
    rows = eod_report.collect(st, {"AAA": 100, "BBB": 50})
    tot = eod_report.totals(rows)["all"]
    assert tot["n"] == 2 and tot["gross"] == -200 + 250
    assert any(r["twin_of_live"] for r in rows)


def test_missing_lot_gives_no_pnl():
    rows = eod_report.collect({"shadow_trades": {"c": _t("ZZZ", 20.0, 18.0, 25.0, "T2_HIT")}}, {})
    assert rows[0]["pnl_gross"] is None
