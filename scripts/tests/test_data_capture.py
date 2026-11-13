import gzip
import json
from datetime import datetime

import candle_archive as CA
import market_context as MC
import mcx_main


def test_candle_archive_merges_and_dedupes(tmp_path):
    CA._BUF.clear()
    CA.record("MCX|1", [{"timestamp": "2026-11-13T10:00:00+05:30", "open": 1, "high": 2, "low": 0.5, "close": 1.5}])
    p = tmp_path / "c" / "d.json.gz"
    assert CA.flush(p) == 1
    CA.record("MCX|1", [{"timestamp": "2026-11-13T10:01:00+05:30", "open": 2, "high": 3, "low": 1, "close": 2}])
    CA.record("MCX|1", [{"timestamp": "2026-11-13T10:00:00+05:30", "open": 1, "high": 2, "low": 0.5, "close": 1.5}])
    CA.flush(p)
    data = json.load(gzip.open(p, "rt"))
    assert sorted(data["MCX|1"]) == ["10:00", "10:01"] and CA._BUF == {}
    assert CA.flush(tmp_path / "none.gz") == 0            # nothing buffered: no file, no error


def test_candle_archive_survives_corrupt_file_and_bad_input(tmp_path):
    p = tmp_path / "d.json.gz"
    p.write_bytes(b"not gzip")
    CA._BUF.clear()
    CA.record("K", [{"timestamp": "2026-11-13T10:00:00+05:30", "open": 1, "high": 1, "low": 1, "close": 1}])
    CA.record(None, [])
    CA.record("K", [{"bad": 1}])
    assert CA.flush(p) == 1


def test_day_type_classification():
    assert MC.day_type(100, 101, 99.9, 100.9, 100)[0] == "trend_up"
    assert MC.day_type(100, 100.1, 99, 99.1, 100)[0] == "trend_down"
    assert MC.day_type(100, 100.5, 99.5, 100.05, 100)[0] == "range"
    assert MC.day_type(100, 100.5, 99.5, 100.4, 100)[0] == "mixed"
    assert MC.day_type(101, 101.5, 100.5, 101.4, 100)[0].startswith("gap_up")
    assert "volatile" in MC.day_type(100, 102, 99, 101.9, 100)[0] and MC.day_type(100, 102, 99, 101.9, 100)[0].endswith("trend_up")
    assert MC.day_type(0, 1, 1, 1, 1)[0] == "unknown"


def test_record_day_type_once_after_close(tmp_path):
    class MD:
        def get_recent_daily_candles(self, key, n):
            return [{"timestamp": "2026-11-12T00:00:00", "close": 100},
                    {"timestamp": "2026-11-13T00:00:00", "open": 100, "high": 102, "low": 99.9, "close": 101.9}]
    p = tmp_path / "day_types.csv"
    assert not MC.record_day_type(MD(), "2026-11-13", datetime(2026, 11, 13, 15, 0), p)          # market still open
    assert MC.record_day_type(MD(), "2026-11-13", datetime(2026, 11, 13, 15, 40), p)
    assert not MC.record_day_type(MD(), "2026-11-13", datetime(2026, 11, 13, 15, 45), p)         # once per day
    assert "trend_up" in p.read_text()


def test_wide_sl_twin_doubles_stop_distance_and_is_silent():
    st = {"mcx_shadow_trades": {}, "next_seq": {}}
    setup = {"alert_premium": 100.0, "entry_low": 100.0, "entry_high": 100.0, "stop_loss": 96.0, "target_1": 108.0, "target_2": 112.0, "strategy": "bos", "symbol": "GOLDM",
             "direction": "CE", "instrument_key": "MCX|9", "strike": 1, "expiry": "x", "spot_price": 1, "moneyness": "ATM",
             "grade": "Shadow", "trade_score": 50, "probability_pct": None, "probability_label": "Shadow", "score_breakdown": [],
             "confidence_score": 5.0, "risk_score": 1.0, "reward_score": 1.0, "expected_rr": "1:1", "factor_checklist": [],
             "confidence_stars": "", "confidence_label": "Shadow", "risk_rating": "", "risk_label": "x",
             "expected_holding_minutes": [30, 180], "why_reasons": [], "session": "MCX", "alternatives": []}
    base = {"trade_id": "B", "probability_status": "shadow_only"}
    mcx_main._wide_sl_twin(st, base, setup, "MCX-bos-GOLDM", "CE", datetime(2026, 11, 13, 10, 0))
    (tw,) = st["mcx_shadow_trades"].values()
    assert tw["strategy"] == "bos_wsl" and tw["stop_loss"] == 92.0 and tw["target_1"] == 108.0 and tw["variant_of"] == "B"
    sent = []
    mcx_main.tg.send = lambda m, *a, **k: sent.append(m)
    mcx_main.notify_events(tw, [{"type": "SL_HIT"}])
    assert sent == []                                    # twins never message
