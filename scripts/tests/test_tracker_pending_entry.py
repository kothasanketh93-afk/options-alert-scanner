from tracker import process_trade_candles


def _trade(**kw):
    t = {"status": "WAITING_ENTRY", "entry_low": 100.0, "entry_high": 100.0, "stop_loss": 90.0,
         "target_1": 120.0, "target_2": 130.0, "alert_time": "2026-11-06T18:45:00+05:30",
         "last_processed_ts": "2026-11-06T18:45:00+05:30", "display_label": "x",
         "market_close_hhmm": [23, 25]}
    t.update(kw)
    return t


def _c(hhmm, o, h, l, c):
    return {"timestamp": f"2026-11-06T{hhmm}:00+05:30", "open": o, "high": h, "low": l, "close": c}


def test_stop_level_traded_before_entry_cancels():
    t = _trade()
    ev = process_trade_candles(t, [_c("18:46", 95, 96, 85, 88), _c("19:00", 90, 105, 99, 101)])
    assert t["status"] == "NOT_ENTERED" and t["not_entered_reason"] == "SL_BREACHED_BEFORE_ENTRY"
    assert [e["type"] for e in ev] == ["NOT_ENTERED"]


def test_gap_over_entry_to_t1_is_missed_move():
    t = _trade()
    process_trade_candles(t, [_c("18:46", 110, 125, 108, 121)])
    assert t["not_entered_reason"] == "T1_REACHED_BEFORE_ENTRY"


def test_entry_window_expires():
    t = _trade()
    process_trade_candles(t, [_c("19:00", 110, 112, 108, 111), _c("19:06", 110, 112, 108, 111)])
    assert t["status"] == "NOT_ENTERED" and t["not_entered_reason"] == "ENTRY_WINDOW_EXPIRED"


def test_fill_inside_window_still_enters_and_candle_touching_beats_cancel():
    t = _trade()
    process_trade_candles(t, [_c("18:50", 105, 106, 99, 101)])
    assert t["status"] == "ACTIVE"
    t2 = _trade()
    process_trade_candles(t2, [_c("18:50", 105, 106, 85, 101)])      # touches entry AND stop in one bar
    assert t2["status"] == "ACTIVE"


def test_waiting_inside_window_stays_pending():
    t = _trade()
    process_trade_candles(t, [_c("18:50", 110, 112, 108, 111)])
    assert t["status"] == "WAITING_ENTRY"
