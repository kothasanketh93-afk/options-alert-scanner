import csv
import market_context
import outcomes_ledger


class _B:
    def get_ltp(self, k): return 25200.0


class _MD:
    _broker = _B()

    @staticmethod
    def get_recent_daily_candles(k, n):
        return [{"timestamp": "2026-11-05T00:00:00+05:30", "close": 25000.0},
                {"timestamp": "2026-11-06T00:00:00+05:30", "close": 25190.0}]


def test_nifty_change_uses_previous_close():
    assert market_context.nifty_change_pct(_MD, "2026-11-06") == 0.8


def test_stamp_once_and_ledger_columns(tmp_path):
    t = {"trade_id": "A1", "status": "SL_HIT", "final_status": "SL_HIT", "symbol": "NIFTY", "direction": "CE",
         "strategy": "s", "alert_time": "2026-11-06T10:00:30+05:30", "tracked_entry_price": 100.0,
         "stop_loss": 90.0, "target_1": 110.0, "target_2": 120.0, "entry_low": 100, "entry_high": 100}
    st = {"date": "2026-11-06", "shadow_trades": {"A1": t}}
    assert market_context.stamp_trades(st, 17.5, 0.8) == 1
    assert market_context.stamp_trades(st, 99, 9) == 0           # already stamped: first value stays
    p = tmp_path / "o.csv"
    assert outcomes_ledger.append_resolved(st, p) == 1
    row = next(csv.DictReader(open(p)))
    assert row["vix_at_alert"] == "17.5" and row["nifty_chg_pct_at_alert"] == "0.8"
    assert row["setup_key"] == "2026-11-06|main|s|NIFTY|CE|2026-11-06T10:00"
