import csv, gzip, json, os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import weekly_report, weekly_image
from outcomes_ledger import COLUMNS


def _setup(tmp):
    os.makedirs(os.path.join(tmp, "instrument_master"))
    for d in ("2026-11-02", "2026-11-03"):
        with gzip.open(os.path.join(tmp, "instrument_master", f"{d}.json.gz"), "wt") as f:
            json.dump({"rows": [{"option_type": "CE", "underlying_symbol": "AAA", "lot_size": 100},
                                {"option_type": "CE", "underlying_symbol": "NIFTY", "lot_size": 50}]}, f)
    rows = [
        dict(trade_id="1", date="2026-11-02", engine="live", strategy="trade_score", symbol="AAA", direction="CE", strike="100",
             alert_time="2026-11-02T09:40:00", entry_price="10", final_status="T2_HIT", t1_hit="True", result_pct="20", target_1="12",
             entry_bucket="0915-0945"),
        dict(trade_id="2", date="2026-11-02", engine="main", strategy="trade_score", symbol="AAA", direction="CE", strike="100",
             alert_time="2026-11-02T09:40:00", entry_price="10", final_status="T2_HIT", t1_hit="True", result_pct="20", target_1="12",
             entry_bucket="0915-0945"),   # duplicate of the live row
        dict(trade_id="3", date="2026-11-03", engine="fib_pivot", strategy="trade_score", symbol="NIFTY", direction="PE", strike="24000",
             alert_time="2026-11-03T10:00:00", entry_price="100", final_status="SL_HIT", t1_hit="False", result_pct="-10", target_1="120",
             entry_bucket="0945-1130"),
    ]
    p = os.path.join(tmp, "o.csv")
    with open(p, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    return p


def test_weekly_collect_dedupes_and_sums():
    with tempfile.TemporaryDirectory() as tmp:
        p = _setup(tmp)
        data = weekly_report.collect(p, os.path.join(tmp, "instrument_master"), "2026-11-05")
        assert data["start"] == "2026-11-02" and len(data["trades"]) == 2
        s = weekly_report.summarize(data)
        assert s["all"]["gross"] == round(10 * 100 * 0.2) + round(100 * 50 * -0.10) == 200 - 500
        assert s["all"]["wins"] == 1 and {e["label"] for e in s["engines"]} == {"LIVE", "Fib pivot"}
        assert weekly_image.render(data, os.path.join(tmp, "w.png")) and os.path.getsize(os.path.join(tmp, "w.png")) > 1000


def test_ledger_context_columns_and_backfill(tmp_path, monkeypatch):
    import outcomes_ledger as ol
    events = tmp_path / "events.csv"
    events.write_text("# date,scope,note\ndate,scope,note\n2026-11-06,NSE,RBI test\n2026-11-06,ALL,Holiday-eve\n")
    monkeypatch.setattr(ol, "EVENTS_FILE", events)
    assert ol.day_tags("2026-11-06", "NIFTY") == "RBI test;Holiday-eve"
    assert ol.day_tags("2026-11-06", "GOLD") == "US_PAYROLLS;Holiday-eve"          # first Friday, MCX symbol; NSE-only event excluded
    assert ol.day_tags("2026-11-04", "CRUDEOIL") == "EIA_CRUDE" and ol.day_tags("2026-11-05", "NATURALGAS") == "EIA_GAS_STORAGE"
    ctx = ol.derive_context({"alert_time": "2026-11-05T09:40:00", "actual_entry_time": "2026-11-05T09:47:30", "entry_price": "100",
                             "stop_loss": "90", "target_1": "115", "symbol": "NIFTY", "date": "2026-11-05"})
    assert ctx["weekday"] == "Thu" and ctx["signal_delay_min"] == 7.5 and ctx["risk_pct"] == 10.0 and ctx["rr_t1"] == 1.5
    trade = {"trade_id": "t", "symbol": "NIFTY", "direction": "CE", "strike": 24000, "alert_time": "2026-11-05T09:40:00",
             "actual_entry_time": "2026-11-05T09:45:00", "alert_premium": 100, "tracked_entry_price": 100, "stop_loss": 90, "target_1": 115,
             "target_2": 130, "final_status": "SL_HIT", "status": "SL_HIT", "sl_hit_time": "2026-11-05T10:15:00", "spot_price": 24010}
    row = ol._row(trade, "main", "2026-11-05")
    assert row["hold_min"] == 30.0 and row["exit_time"] == "2026-11-05T10:15:00" and row["spot_at_alert"] == 24010
    assert set(row) <= set(ol.COLUMNS)
