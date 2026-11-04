import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import outcomes_ledger as L


def _t(**kw):
    base = {"tracked_entry_price": 100.0, "target_1": 120.0, "t1_hit": True}
    base.update(kw)
    return base


def test_trail_variants():
    assert L.trail_variant(_t(t1_hit=False), -10.0) == -10.0
    assert L.trail_variant(_t(trail_exit_price=110.0, trail_sl=110.0), -20.0) == 10.0   # trail exit beats the original SL loss
    assert L.trail_variant(_t(trail_sl=105.0), 35.0) == 35.0                              # never trailed out: actual outcome
    assert L.trail_variant(_t(), -20.0) is None                                           # old record without trail data


def test_tracker_records_trail_exit():
    import tracker
    t = {"status": "T1_HIT", "t1_hit": True, "tracked_entry_price": 100.0, "stop_loss": 90.0, "target_1": 110.0,
         "target_2": 140.0, "highest_since_entry": 110.0, "lowest_since_entry": 100.0, "max_favorable_pct": 10.0,
         "max_adverse_pct": 0.0, "last_processed_ts": "2026-11-04T10:00:00+05:30"}
    c = lambda ts, o, h, l, cl: {"timestamp": f"2026-11-04T{ts}:00+05:30", "open": o, "high": h, "low": l, "close": cl, "volume": 1}
    # dist = 0.5*(110-100) = 5. High 120 -> trail 115; next candle dips to 114 -> exits at 115 (+15%), live SL untouched
    tracker.process_trade_candles(t, [c("10:01", 112, 120, 111, 118), c("10:02", 118, 119, 114, 116)])
    assert t["trail_exit_price"] == 115.0 and t["status"] == "T1_HIT"
    assert L.trail_variant(t, -10.0) == 15.0
