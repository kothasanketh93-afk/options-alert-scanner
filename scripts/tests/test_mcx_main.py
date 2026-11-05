import os, sys
from datetime import datetime
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import mcx_main, tracker


def _row(name, kind, strike, exp="17Nov2026", itype="OPTFUT", tok="1"):
    return {"exch_seg": "MCX", "name": name, "instrumenttype": itype, "expiry": exp, "strike": str(strike * 100.0),
            "symbol": f"{name}{exp}{strike}{kind}", "token": tok, "lotsize": "100"}


def test_resolve_contracts_skips_expiry_day_and_picks_nearest():
    m = [_row("CRUDEOIL", "CE", 8500, "17Nov2026", tok="a"), _row("CRUDEOIL", "CE", 8500, "16Dec2026", tok="b"),
         _row("CRUDEOIL", "", 0, "19Nov2026", "FUTCOM", "f")]
    now = datetime(2026, 11, 17, 10, 0)
    c = mcx_main.resolve_contracts(m, "CRUDEOIL", now)           # 17 Nov is expiry day -> next expiry
    assert c["expiry"] == "2026-12-16" and c["chain"][0]["_strike"] == 8500.0 and c["fut"]["token"] == "f"
    assert mcx_main.resolve_contracts(m, "GOLD", now) is None


def test_session_window():
    assert mcx_main.in_session(datetime(2026, 11, 5, 21, 0)) and not mcx_main.in_session(datetime(2026, 11, 7, 12, 0))
    assert mcx_main.in_session(datetime(2026, 11, 5, 23, 45)) and not mcx_main.in_session(datetime(2026, 11, 5, 8, 30))


def test_tracker_uses_trade_specific_close():
    from datetime import timezone, timedelta
    ist = timezone(timedelta(hours=5, minutes=30))
    dt = datetime(2026, 11, 5, 20, 0, tzinfo=ist)
    assert tracker._is_after_market_close(dt)                      # NSE default: after 15:30
    assert not tracker._is_after_market_close(dt, (23, 25))        # MCX trade: still open
    assert tracker._is_after_market_close(datetime(2026, 11, 5, 23, 26, tzinfo=ist), (23, 25))


def test_summary_text_without_trades():
    assert "no closed trades" in mcx_main.summary_text({"date": "2026-11-05", "mcx_shadow_trades": {}, "rejects": {}})


def test_mcx_report_week_and_empty_day():
    import csv, tempfile
    import mcx_report
    from outcomes_ledger import COLUMNS
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "o.csv")
        with open(p, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=COLUMNS)
            w.writeheader()
            w.writerow(dict(trade_id="1", date="2026-11-05", engine="mcx", strategy="fvg", symbol="GOLDM", direction="PE", strike="144000",
                            alert_time="2026-11-05T18:10:00", entry_price="2000", final_status="T2_HIT", t1_hit="True", result_pct="10", target_1="2100"))
        data = mcx_report.collect_week(p, "2026-11-05")
        assert len(data["trades"]) == 1 and round(data["trades"][0]["gross"]) == 2000 and data["trades"][0]["bucket"] == "1700-2100"
        assert mcx_report.render_weekly(p, "2026-11-05", os.path.join(tmp, "w.png"))
        assert not mcx_report.render_daily({"date": "2026-11-05", "mcx_shadow_trades": {}}, os.path.join(tmp, "d.png"))
