import os, sys
_here = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(_here))
import tracker


def _trade():
    return {"status": "T1_HIT", "t1_hit": True, "tracked_entry_price": 100.0, "stop_loss": 90.0, "target_1": 110.0,
            "target_2": 125.0, "highest_since_entry": 110.0, "lowest_since_entry": 100.0, "max_favorable_pct": 10.0,
            "max_adverse_pct": 0.0, "last_processed_ts": "2026-11-03T10:00:00+05:30"}


def _c(ts, o, h, l, c):
    return {"timestamp": f"2026-11-03T{ts}:00+05:30", "open": o, "high": h, "low": l, "close": c, "volume": 1}


def test_marks_return_to_entry():
    t = _trade()
    tracker.process_trade_candles(t, [_c("10:01", 108, 112, 106, 109), _c("10:02", 106, 108, 99.5, 101)])
    assert t["be_tracked"] and t["be_touch_price"] == 100.0 and t["status"] == "T1_HIT"


def test_no_touch_when_price_holds():
    t = _trade()
    tracker.process_trade_candles(t, [_c("10:01", 108, 112, 106, 109), _c("10:02", 109, 126, 108, 125)])
    assert t["be_tracked"] and "be_touch_price" not in t and t["final_status"] == "T2_HIT"
