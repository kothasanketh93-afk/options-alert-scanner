from datetime import datetime

import pnl_snapshot as P
import eod_report


def at(h, m):
    return datetime(2026, 11, 9, h, m)


def test_due_slot_one_per_run_no_burst():
    sent = []
    assert P.due_slot(at(10, 10), P.NSE_SLOTS, sent) is None
    assert P.due_slot(at(13, 40), P.NSE_SLOTS, sent) == "1315"           # earlier slots were missed: only the latest slot is due
    P.mark_sent(at(13, 40), P.NSE_SLOTS, sent)
    assert P.due_slot(at(13, 45), P.NSE_SLOTS, sent) is None
    assert sent == ["1015", "1115", "1215", "1315"] or sent[-1] == "1315"
    assert P.due_slot(at(15, 16), P.NSE_SLOTS, sent) == "1515"


def _trade(status, entry, last=None, sl=None, t2=None, sym="NIFTY", strike=25000):
    return {"symbol": sym, "direction": "CE", "strike": strike, "alert_time": "2026-11-09T10:00:00+05:30", "status": status,
            "final_status": status if status not in P.OPEN_STATUSES else None, "tracked_entry_price": entry,
            "last_price": last, "stop_loss": sl, "target_2": t2, "target_1": t2}


def test_summarize_closed_and_open_one_lot():
    st = {"shadow_trades": {"a": _trade("T2_HIT", 100, t2=120), "b": _trade("SL_HIT", 100, sl=90, strike=25050),
                            "c": _trade("ACTIVE", 100, last=105, strike=25100), "d": _trade("WAITING_ENTRY", None, strike=25150)}}
    rows = eod_report.collect(st, {"NIFTY": 75})
    s = P.summarize(rows, lambda r: r["engine"])
    g = s["Main shadow"]
    assert g["closed_n"] == 2 and g["wins"] == 1 and g["gross"] == (20 - 10) * 75
    assert g["open_n"] == 1 and g["mtm"] == 5 * 75
    msg = P.format_message("NSE", "1115", s)
    assert "P&L snapshot 11:15 IST" in msg and "Total now" in msg and "Main shadow" in msg


def test_image_renders(tmp_path):
    import pnl_snapshot_image as I
    st = {"shadow_trades": {"a": _trade("T2_HIT", 100, t2=120), "c": _trade("ACTIVE", 100, last=105, strike=25100)}}
    rows = eod_report.collect(st, {"NIFTY": 75})
    out = tmp_path / "p.png"
    assert I.render("NSE", "2026-11-09", "1115", P.summarize(rows, lambda r: r["engine"]), P.open_positions(rows), str(out), "Engine")
    assert out.stat().st_size > 5000
